import os
import re
import json
import pickle
from datetime import datetime, timedelta
from flask import Flask, request, Response

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

app = Flask(__name__)

SCOPES = ['https://www.googleapis.com/auth/calendar']
CALENDAR_ID = 'primary'
TOKEN_FILE = 'token.pickle'
LEMBRETES_FILE = 'lembretes.json'

CORES = {
    'vermelho': '11', 'laranja': '6', 'amarelo': '5',
    'verde': '2', 'azul': '1', 'roxo': '3', 'cinza': '8',
}

CORES_NOMES = {
    'vermelho': '🔴 Pessoal', 'laranja': '🟠 Reunião', 'amarelo': '🟡 Importante',
    'verde': '🟢 Estudo', 'azul': '🔵 Geral', 'roxo': '🟣 Lazer', 'cinza': '⚫ Outro',
}

EMOJI_CORES = {'11': '🔴', '6': '🟠', '5': '🟡', '2': '🟢', '1': '🔵', '3': '🟣', '8': '⚫', '': ''}

PALAVRAS_CORES = {
    'vermelho': ['pessoal', 'academia', 'treino', 'médico', 'medico', 'dentista'],
    'laranja': ['reunião', 'reuniao', 'call', 'trabalho', 'escritório', 'cliente'],
    'verde': ['estudo', 'curso', 'aula', 'faculdade', 'escola'],
    'roxo': ['lazer', 'cinema', 'show', 'festa', 'aniversário'],
    'amarelo': ['importante', 'urgente', 'deadline', 'prazo'],
}

user_sessions = {}

def get_calendar_service():
    print(f"🔍 Procurando token em: {os.path.abspath(TOKEN_FILE)}")
    print(f"📁 Arquivos na pasta: {os.listdir('.')}")
    
    creds = None
    
    # Verificar se token existe
    if os.path.exists(TOKEN_FILE):
        print(f"✅ Token encontrado!")
        with open(TOKEN_FILE, 'rb') as token:
            creds = pickle.load(token)
        print(f"📊 Token válido: {creds.valid if creds else 'None'}")
    else:
        print(f"❌ Token NÃO encontrado em: {TOKEN_FILE}")
        return None

    if not creds or not creds.valid:
        print(f"⚠️ Token inválido ou expirado")
        if creds and creds.expired and creds.refresh_token:
            print(f"🔄 Tentando refresh...")
            try:
                creds.refresh(Request())
                # Salvar token atualizado
                with open(TOKEN_FILE, 'wb') as token:
                    pickle.dump(creds, token)
                print(f"✅ Token atualizado!")
            except Exception as e:
                print(f"❌ Erro no refresh: {e}")
                return None
        else:
            print(f"❌ Sem refresh token disponível")
            return None

    print(f"✅ Autenticação OK!")
    return build('calendar', 'v3', credentials=creds)

def parse_date(text, base_date=None):
    if base_date is None:
        base_date = datetime.now()
    text_lower = text.lower().strip()
    
    if text_lower == 'hoje':
        return base_date.replace(hour=0, minute=0, second=0, microsecond=0)
    if text_lower in ['amanhã', 'amanha']:
        return (base_date + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    
    dias = {'segunda': 0, 'terça': 1, 'quarta': 2, 'quinta': 3, 'sexta': 4, 'sábado': 5, 'domingo': 6}
    for dia, num in dias.items():
        if dia in text_lower:
            dias_ahead = num - base_date.weekday()
            if dias_ahead <= 0:
                dias_ahead += 7
            return (base_date + timedelta(days=dias_ahead)).replace(hour=0, minute=0, second=0, microsecond=0)
    
    match = re.search(r'(\d{1,2})[\/\-\.](\d{1,2})(?:[\/\-\.](\d{2,4}))?', text_lower)
    if match:
        dia, mes, ano = int(match.group(1)), int(match.group(2)), int(match.group(3) or base_date.year)
        if ano < 100:
            ano += 2000
        try:
            return datetime(ano, mes, dia)
        except:
            pass
    return None

def parse_time(text):
    text_lower = text.lower()
    match = re.search(r'(\d{1,2})[:h](\d{2})', text_lower)
    if match:
        h, m = int(match.group(1)), int(match.group(2))
        if 0 <= h <= 23 and 0 <= m <= 59:
            return f"{h:02d}:{m:02d}"
    return None

def detectar_cor(mensagem):
    texto = mensagem.lower()
    for cor, id_cor in CORES.items():
        if cor in texto:
            return id_cor
    for cor, palavras in PALAVRAS_CORES.items():
        for p in palavras:
            if p in texto:
                return CORES[cor]
    return CORES['azul']

def extract_info(mensagem):
    msg_lower = mensagem.lower()
    is_lembrete = any(x in msg_lower for x in ['me lembra', 'me lembre', 'lembrar'])
    
    info = {
        'tipo': 'lembrete' if is_lembrete else 'evento',
        'titulo': None, 'data': None, 'hora': None,
        'duracao': 60, 'cor': CORES['azul']
    }
    
    info['data'] = parse_date(mensagem)
    info['hora'] = parse_time(mensagem)
    info['cor'] = detectar_cor(mensagem)
    
    # Extrair título
    titulo = mensagem
    titulo = re.sub(r'\d{1,2}[\/\-\.]\d{1,2}', '', titulo)
    titulo = re.sub(r'\d{1,2}[:h]\d{2}', '', titulo)
    for cor in CORES.keys():
        titulo = re.sub(r'\b' + cor + r'\b', '', titulo, flags=re.IGNORECASE)
    titulo = re.sub(r'\b(agendar|marcar|lembre|me|de|as|às|dia)\b', '', titulo, flags=re.IGNORECASE)
    titulo = titulo.strip()
    info['titulo'] = titulo.title() if len(titulo) > 2 else "Evento"
    
    return info

# ============ LISTAR EVENTOS - NOVA FUNÇÃO ============

def listar_eventos(periodo="hoje"):
    try:
        service = get_calendar_service()
        if not service:
            return "❌ Erro: Não autenticado no Google Calendar"
        
        now = datetime.now()
        
        if periodo == "hoje":
            inicio = now.replace(hour=0, minute=0, second=0)
            fim = inicio + timedelta(days=1)
            titulo = "📅 *Hoje*"
        elif periodo == "amanha":
            inicio = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0)
            fim = inicio + timedelta(days=1)
            titulo = "📅 *Amanhã*"
        elif periodo == "semana":
            inicio = now
            fim = now + timedelta(days=7)
            titulo = "📅 *Esta Semana*"
        else:
            inicio = now
            fim = now + timedelta(days=30)
            titulo = "📅 *Próximos 30 dias*"
        
        eventos = service.events().list(
            calendarId=CALENDAR_ID,
            timeMin=inicio.isoformat() + 'Z',
            timeMax=fim.isoformat() + 'Z',
            maxResults=20,
            singleEvents=True,
            orderBy='startTime'
        ).execute().get('items', [])
        
        if not eventos:
            return f"{titulo}\n\nNenhum evento encontrado! 🎉"
        
        resposta = f"{titulo}\n\n"
        for evt in eventos:
            inicio_evt = evt['start'].get('dateTime', evt['start'].get('date'))
            
            if 'T' in inicio_evt:
                dt = datetime.fromisoformat(inicio_evt.replace('Z', '+00:00'))
                data_str = dt.strftime('%a %d/%m %H:%M')
            else:
                data_str = inicio_evt
            
            cor_id = evt.get('colorId', '')
            emoji = EMOJI_CORES.get(cor_id, '🔵')
            titulo_evt = evt.get('summary', 'Sem título')
            
            resposta += f"{emoji} {data_str} - {titulo_evt}\n"
        
        return resposta
        
    except Exception as e:
        return f"❌ Erro ao buscar eventos: {str(e)}"

# ============ CRIAR EVENTO ============

def criar_evento(info):
    try:
        service = get_calendar_service()
        if not service:
            return None, "❌ Erro: Não autenticado no Google Calendar"
        
        date_str = info['data'].strftime('%Y-%m-%d')
        start = datetime.strptime(f"{date_str} {info['hora']}", "%Y-%m-%d %H:%M")
        end = start + timedelta(minutes=info['duracao'])
        
        evento = {
            'summary': info['titulo'],
            'colorId': info['cor'],
            'start': {
                'dateTime': start.isoformat(),
                'timeZone': 'America/Sao_Paulo',
            },
            'end': {
                'dateTime': end.isoformat(),
                'timeZone': 'America/Sao_Paulo',
            },
        }
        
        service.events().insert(calendarId=CALENDAR_ID, body=evento).execute()
        
        cor_nome = CORES_NOMES.get([k for k, v in CORES.items() if v == info['cor']][0], '🔵 Geral')
        data_fmt = start.strftime('%d/%m às %H:%M')
        
        return True, f"✅ *Evento criado!*\n{cor_nome}\n📝 {info['titulo']}\n📅 {data_fmt}"
        
    except Exception as e:
        return None, f"❌ Erro: {str(e)}"

def xml_response(texto):
    xml = f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{texto}</Message></Response>'
    return Response(xml, mimetype='text/xml; charset=utf-8')

@app.route('/webhook', methods=['POST'])
def webhook():
    telefone = request.form.get('From', '').replace('whatsapp:', '')
    mensagem = request.form.get('Body', '').strip()
    msg_lower = mensagem.lower()
    
    print(f"📨 {telefone}: {mensagem}")
    
    if telefone not in user_sessions:
        user_sessions[telefone] = {'estado': 'livre', 'dados': {}}
    
    sessao = user_sessions[telefone]
    
    # ============ COMANDOS DE LISTAGEM (NOVOS) ============
    
    if any(x in msg_lower for x in ['o que tenho hoje', 'agenda hoje', 'eventos hoje']):
        return xml_response(listar_eventos("hoje"))
    
    if any(x in msg_lower for x in ['o que tenho amanhã', 'agenda amanhã', 'amanhã']):
        return xml_response(listar_eventos("amanha"))
    
    if any(x in msg_lower for x in ['semana', 'próximos dias', 'essa semana']):
        return xml_response(listar_eventos("semana"))
    
    if any(x in msg_lower for x in ['todos eventos', 'próximo mês', 'próximos eventos']):
        return xml_response(listar_eventos("mes"))
    
    # ============ COMANDOS BÁSICOS ============
    
    if any(x in msg_lower for x in ['ajuda', 'help', 'oi', 'olá']):
        return xml_response(
            "👋 *Assistente de Agenda*\n\n"
            "*Comandos:*\n"
            "• `reunião amanhã 15h` - Criar evento\n"
            "• `o que tenho hoje` - Ver agenda de hoje\n"
            "• `agenda da semana` - Ver próximos 7 dias\n"
            "• `me lembre de pagar conta dia 25` - Criar lembrete\n\n"
            "Cores: vermelho, laranja, amarelo, verde, azul, roxo"
        )
    
    # ============ FLUXO DE CRIAÇÃO ============
    
    if sessao['estado'] == 'confirmando':
        if any(x in msg_lower for x in ['sim', 'ok', 'pode']):
            dados = sessao['dados']
            ok, resposta = criar_evento(dados)
            sessao['estado'] = 'livre'
            return xml_response(resposta)
        
        elif any(x in msg_lower for x in ['não', 'nao', 'cancelar']):
            sessao['estado'] = 'livre'
            return xml_response("❌ Cancelado!")
        
        else:
            return xml_response("🤔 Responda *sim* para confirmar ou *não* para cancelar")
    
    # Extrair info nova
    info = extract_info(mensagem)
    
    if info['data'] and info['hora']:
        sessao['estado'] = 'confirmando'
        sessao['dados'] = info
        cor_nome = [v for k, v in CORES_NOMES.items() if CORES[k] == info['cor']][0]
        
        resumo = (f"📅 *Confirmar evento:*\n"
                 f"{cor_nome}\n"
                 f"📝 {info['titulo']}\n"
                 f"📆 {info['data'].strftime('%d/%m/%Y')} às {info['hora']}\n\n"
                 f"✅ Confirma?")
        
        return xml_response(resumo)
    
    # Resposta padrão
    return xml_response(
        "🤔 Não entendi!\n\n"
        "Tente:\n"
        "• `reunião amanhã 15h`\n"
        "• `o que tenho hoje`\n"
        "• `ajuda`"
    )

@app.route('/health', methods=['GET'])
def health():
    return {"status": "ok", "service": "whatsapp-calendar-bot"}

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)

