import os
import json
import re
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
from twilio.twiml.messaging_response import MessagingResponse
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

app = Flask(__name__)

# ============================================================================
# CONFIGURAÇÃO DO GOOGLE CALENDAR - SERVICE ACCOUNT (NUNCA EXPIRA!)
# ============================================================================

SCOPES = ['https://www.googleapis.com/auth/calendar']

def get_calendar_service():
    """
    Autenticação via Service Account - Robusta e sem expiração
    """
    try:
        # FORÇA uso da variável de ambiente - SEM FALLBACK para arquivo
        creds_json = os.environ.get('GOOGLE_CREDENTIALS_JSON')
        
        if not creds_json:
            raise Exception("❌ Variável GOOGLE_CREDENTIALS_JSON não configurada no Render!")
        
        print("🔐 Usando variável de ambiente...")
        creds_info = json.loads(creds_json)
        
        # Valida campos obrigatórios
        required_fields = ['type', 'project_id', 'private_key', 'client_email']
        for field in required_fields:
            if field not in creds_info:
                raise Exception(f"❌ Campo '{field}' ausente no JSON!")
        
        credentials = service_account.Credentials.from_service_account_info(
            creds_info, scopes=SCOPES
        )
        
        service = build('calendar', 'v3', credentials=credentials)
        
        # Testa a conexão imediatamente
        calendars = service.calendarList().list().execute()
        print(f"✅ Conectado ao Google Calendar! Calendários: {len(calendars.get('items', []))}")
        
        return service
        
    except json.JSONDecodeError as e:
        raise Exception(f"❌ JSON inválido: {e}")
    except Exception as e:
        print(f"❌ ERRO CRÍTICO: {e}")
        raise

# Inicializa o serviço globalmente
try:
    calendar_service = get_calendar_service()
    print("🚀 Serviço do Calendar inicializado com sucesso!")
except Exception as e:
    print(f"⚠️ Erro na inicialização: {e}")
    calendar_service = None

# ============================================================================
# CONFIGURAÇÃO DE CORES DOS EVENTOS
# ============================================================================

CORES_EVENTO = {
    'vermelho': '11',    # Pessoal, médico, academia
    'laranja': '6',      # Reuniões, trabalho  
    'amarelo': '5',      # Importante, urgente
    'verde': '10',       # Estudo, curso
    'azul': '9',         # Padrão
    'roxo': '3',         # Lazer, festa
    'rosa': '8',         # Aniversários
}

PALAVRAS_CHAVE_CORES = {
    'vermelho': ['academia', 'médico', 'medico', 'consulta', 'remédio', 'remedio', 'pessoal', 'corrida', 'musculação', 'musculacao', 'treino'],
    'laranja': ['reunião', 'reuniao', 'trabalho', 'escritório', 'escritorio', 'cliente', 'call', 'videochamada', 'standup', 'meet', 'zoom'],
    'amarelo': ['importante', 'urgente', 'prazo', 'deadline', 'entrega', 'pagamento', 'conta', 'vencimento'],
    'verde': ['estudo', 'curso', 'aula', 'faculdade', 'prova', 'treinamento', 'workshop', 'estudar', 'aprender'],
    'roxo': ['lazer', 'festa', 'bar', 'restaurante', 'cinema', 'show', 'viagem', 'feriado', 'parque', 'praia'],
    'rosa': ['aniversário', 'aniversario', 'niver', 'festa de', 'bodas', 'casamento', 'formatura'],
}

def detectar_cor_evento(titulo):
    """
    Detecta a cor ideal baseada no título do evento
    """
    titulo_lower = titulo.lower()
    
    for cor, palavras in PALAVRAS_CHAVE_CORES.items():
        if any(palavra in titulo_lower for palavra in palavras):
            return CORES_EVENTO[cor]
    
    return CORES_EVENTO['azul']  # Padrão

# ============================================================================
# PROCESSAMENTO DE DATAS E HORÁRIOS
# ============================================================================

def parse_data_hora(mensagem):
    """
    Extrai data e hora da mensagem do usuário
    Retorna: (titulo, data_inicio, data_fim) ou None se inválido
    """
    hoje = datetime.now()
    mensagem_lower = mensagem.lower().strip()
    
    # Remove prefixos comuns
    mensagem_lower = re.sub(r'^(me\s+lembre\s+(de\s+)?|criar\s+|adicionar\s+|marcar\s+)', '', mensagem_lower)
    
    # Dicionário de dias da semana
    dias_semana = {
        'segunda': 0, 'segunda-feira': 0, 'terça': 1, 'terca': 1, 'terça-feira': 1, 
        'quarta': 2, 'quarta-feira': 2, 'quinta': 3, 'quinta-feira': 3, 
        'sexta': 4, 'sexta-feira': 4, 'sábado': 5, 'sabado': 5, 'domingo': 6
    }
    
    # Padrões de hora (14h, 14:00, 14h30, 14:30, etc)
    padrao_hora = r'(\d{1,2})[:h]?(\d{2})?(?:\s*h)?(?:\s*(?:da\s*)?(manhã|manha|tarde|noite|madrugada))?'
    
    data_evento = None
    mensagem_processada = mensagem_lower
    
    # Detectar "amanhã"
    if 'amanhã' in mensagem_lower or 'amanha' in mensagem_lower:
        data_evento = hoje + timedelta(days=1)
        mensagem_processada = re.sub(r'amanh[ãa]', '', mensagem_processada)
    
    # Detectar "depois de amanhã"
    elif 'depois de amanhã' in mensagem_lower or 'depois de amanha' in mensagem_lower:
        data_evento = hoje + timedelta(days=2)
        mensagem_processada = re.sub(r'depois de amanh[ãa]', '', mensagem_processada)
    
    # Detectar dias da semana
    elif any(dia in mensagem_lower for dia in dias_semana.keys()):
        for dia_nome, dia_numero in dias_semana.items():
            if dia_nome in mensagem_lower:
                dias_ate = (dia_numero - hoje.weekday()) % 7
                if dias_ate == 0:  # Hoje é esse dia, vai para próxima semana
                    dias_ate = 7
                data_evento = hoje + timedelta(days=dias_ate)
                mensagem_processada = mensagem_processada.replace(dia_nome, '')
                break
    
    # Detectar "hoje"
    elif 'hoje' in mensagem_lower:
        data_evento = hoje
        mensagem_processada = re.sub(r'hoje', '', mensagem_processada)
    
    # Detectar data específica (dia 25, dia 15/03, 15/03, etc)
    elif re.search(r'(?:dia\s+)?(\d{1,2})(?:/(\d{1,2}))?(?:/(\d{2,4}))?', mensagem_lower):
        match = re.search(r'(?:dia\s+)?(\d{1,2})(?:/(\d{1,2}))?(?:/(\d{2,4}))?', mensagem_lower)
        dia = int(match.group(1))
        mes = int(match.group(2)) if match.group(2) else hoje.month
        ano = int(match.group(3)) if match.group(3) else hoje.year
        
        # Ajusta ano se for 2 dígitos
        if ano < 100:
            ano = 2000 + ano
            
        # Ajusta ano se a data já passou
        try:
            data_teste = datetime(ano, mes, dia)
            if data_teste < hoje.replace(hour=0, minute=0, second=0, microsecond=0):
                ano += 1
                data_teste = datetime(ano, mes, dia)
            data_evento = data_teste
        except ValueError:
            return None  # Data inválida
        
        mensagem_processada = mensagem_processada[:match.start()] + mensagem_processada[match.end():]
    
    else:
        return None  # Não conseguiu detectar data
    
    if not data_evento:
        return None
    
    # Extrair hora
    match_hora = re.search(padrao_hora, mensagem_processada, re.IGNORECASE)
    
    if match_hora:
        hora = int(match.group(1))
        minuto = int(match.group(2)) if match.group(2) else 0
        periodo = match.group(3)
        
        # Ajusta período
        if periodo:
            periodo_lower = periodo.lower()
            if periodo_lower in ['tarde'] and hora < 12:
                hora += 12
            elif periodo_lower in ['noite', 'madrugada'] and hora < 12:
                hora += 12
            elif periodo_lower in ['manhã', 'manha'] and hora > 12:
                hora = hora  # Mantém como está
        
        # Validação
        if hora > 23:
            hora = 12
        if minuto > 59:
            minuto = 0
            
        data_inicio = data_evento.replace(hour=hora, minute=minuto, second=0, microsecond=0)
    else:
        # Se não achou hora, assume 9h da manhã
        data_inicio = data_evento.replace(hour=9, minute=0, second=0, microsecond=0)
    
    # Se a hora já passou hoje, assume amanhã à mesma hora
    if data_inicio < hoje:
        data_inicio = data_inicio + timedelta(days=1)
    
    # Duração padrão: 1 hora
    data_fim = data_inicio + timedelta(hours=1)
    
    # Extrair título
    titulo = re.sub(padrao_hora, '', mensagem_processada, flags=re.IGNORECASE)
    titulo = re.sub(r'\s+', ' ', titulo).strip()
    titulo = titulo.replace('dia', '').replace('  ', ' ').strip()
    titulo = titulo.title()  # Primeira letra maiúscula
    
    # Se não sobrou título, usa genérico
    if not titulo or len(titulo) < 2:
        titulo = "Evento"
    
    return titulo, data_inicio, data_fim

# ============================================================================
# CRIAÇÃO DE EVENTOS
# ============================================================================

def criar_evento_calendar(titulo, inicio, fim, descricao="Criado via WhatsApp Bot"):
    """
    Cria o evento no Google Calendar usando Service Account
    """
    global calendar_service
    
    if not calendar_service:
        # Tenta reinicializar
        try:
            calendar_service = get_calendar_service()
        except Exception as e:
            return {
                'sucesso': False,
                'erro': f"Serviço não disponível: {str(e)}"
            }
    
    cor_id = detectar_cor_evento(titulo)
    
    evento = {
        'summary': titulo,
        'description': descricao,
        'start': {
            'dateTime': inicio.isoformat(),
            'timeZone': 'America/Sao_Paulo',
        },
        'end': {
            'dateTime': fim.isoformat(),
            'timeZone': 'America/Sao_Paulo',
        },
        'colorId': cor_id,
        'reminders': {
            'useDefault': False,
            'overrides': [
                {'method': 'popup', 'minutes': 30},
            ],
        },
    }
    
    try:
        evento_criado = calendar_service.events().insert(
            calendarId='primary',
            body=evento
        ).execute()
        
        return {
            'sucesso': True,
            'link': evento_criado.get('htmlLink'),
            'id': evento_criado.get('id'),
            'cor': cor_id,
            'titulo': titulo,
            'inicio': inicio.strftime('%d/%m/%Y %H:%M')
        }
        
    except HttpError as e:
        error_details = str(e)
        print(f"❌ Erro HTTP: {error_details}")
        return {
            'sucesso': False,
            'erro': f"Erro Google Calendar: {error_details}"
        }
    except Exception as e:
        print(f"❌ Erro inesperado: {str(e)}")
        return {
            'sucesso': False,
            'erro': str(e)
        }

# ============================================================================
# ROTAS DO FLASK / TWILIO
# ============================================================================

@app.route("/webhook", methods=['POST'])
def webhook():
    """
    Recebe mensagens do WhatsApp via Twilio
    """
    try:
        mensagem = request.values.get('Body', '').strip()
        numero = request.values.get('From', '')
        
        print(f"📨 {numero}: {mensagem}")
        
        resp = MessagingResponse()
        
        # Comando de ajuda
        if mensagem.lower() in ['ajuda', 'help', 'comandos', 'menu', 'h', '?']:
            resposta = """🤖 *Bot de Agenda - Comandos*

*Criar eventos:*
• "reunião amanhã 15h"
• "academia segunda 6h" 
• "médico dia 25/03 14h"
• "aniversário mãe sexta 19h"

*Cores automáticas:*
🔴 Vermelho: academia, médico, pessoal
🟠 Laranja: reunião, trabalho, call
🟡 Amarelo: importante, urgente, prazo
🟢 Verde: estudo, curso, aula
🔵 Azul: padrão
🟣 Roxo: lazer, festa, bar

*Outros comandos:*
• "status" - Verifica conexão
• "ajuda" - Mostra este menu"""
            resp.message(resposta)
            return str(resp)
        
        # Comando de status/diagnóstico
        if mensagem.lower() in ['status', 'teste', 'ping', 'oi', 'olá', 'ola']:
            try:
                if calendar_service:
                    cals = calendar_service.calendarList().list().execute()
                    resposta = f"""✅ *Bot Online!*

📅 Calendários: {len(cals.get('items', []))}
🤖 Serviço: Ativo
⏰ {datetime.now().strftime('%H:%M:%S')}

Tudo pronto para criar eventos! 🚀"""
                else:
                    resposta = "⚠️ *Atenção:* Serviço do Calendar não inicializado. Verifique os logs."
            except Exception as e:
                resposta = f"❌ *Erro:* {str(e)[:200]}"
            
            resp.message(resposta)
            return str(resp)
        
        # Tenta criar evento
        resultado_parse = parse_data_hora(mensagem)
        
        if resultado_parse:
            titulo, inicio, fim = resultado_parse
            
            # Confirmação antes de criar
            emoji_cor = {'11': '🔴', '6': '🟠', '5': '🟡', '10': '🟢', '9': '🔵', '3': '🟣', '8': '🟣'}.get(detectar_cor_evento(titulo), '🔵')
            
            resposta_confirmacao = f"""📅 *Confirmar evento:*

{emoji_cor} *{titulo}*
🗓️ {inicio.strftime('%d/%m/%Y às %H:%M')}

✅ Responda "sim" para confirmar"""
            
            # Armazena temporariamente (em produção use Redis/DB)
            # Por simplicidade, vamos criar direto e mostrar confirmação
            
            resultado = criar_evento_calendar(titulo, inicio, fim)
            
            if resultado['sucesso']:
                resposta = f"""✅ *Evento Criado!*

📌 *{resultado['titulo']}*
📅 {resultado['inicio']}
{emoji_cor} Cor detectada automaticamente

🔗 Ver no Calendar:
{resultado['link']}"""
            else:
                resposta = f"""❌ *Erro ao criar evento*

Erro: {resultado['erro']}

💡 Tente novamente ou digite "status" para verificar."""
        else:
            resposta = """❓ *Não entendi a data/hora.*

Tente formatos como:
• "reunião amanhã 15h"
• "academia segunda 6h"
• "médico dia 25/03 14h"

Digite "ajuda" para mais opções."""
        
        resp.message(resposta)
        return str(resp)
        
    except Exception as e:
        print(f"❌ Erro no webhook: {e}")
        import traceback
        traceback.print_exc()
        resp = MessagingResponse()
        resp.message("❌ Erro interno. Tente novamente ou digite 'ajuda'.")
        return str(resp)

@app.route("/", methods=['GET'])
def health_check():
    """
    Health check para o Render
    """
    return jsonify({
        "status": "online",
        "service": "whatsapp-agenda-bot",
        "calendar_connected": calendar_service is not None,
        "timestamp": datetime.now().isoformat()
    })

@app.route("/test-calendar", methods=['GET'])
def test_calendar():
    """
    Endpoint de teste para verificar conexão com Google Calendar
    """
    try:
        if not calendar_service:
            return jsonify({"error": "Serviço não inicializado"}), 500
        
        now = datetime.utcnow().isoformat() + 'Z'
        events_result = calendar_service.events().list(
            calendarId='primary',
            timeMin=now,
            maxResults=5,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        events = events_result.get('items', [])
        
        return jsonify({
            "status": "ok",
            "calendar_connected": True,
            "events_found": len(events),
            "events": [{"summary": e.get('summary'), "start": e.get('start')} for e in events]
        })
        
    except Exception as e:
        return jsonify({"error": str(e), "calendar_connected": False}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
