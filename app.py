import os
import re
import json
from datetime import datetime, timedelta
from flask import Flask, request, Response

from google.oauth2 import service_account
from googleapiclient.discovery import build

app = Flask(__name__)

# Configuração
SCOPES = ['https://www.googleapis.com/auth/calendar']
CALENDAR_ID = 'primary'
SERVICE_ACCOUNT_FILE = 'service_account.json'

CORES = {
    'vermelho': '11', 'laranja': '6', 'amarelo': '5',
    'verde': '2', 'azul': '1', 'roxo': '3', 'cinza': '8',
}

CORES_NOMES = {
    'vermelho': '🔴 Pessoal', 'laranja': '🟠 Reunião', 'amarelo': '🟡 Importante',
    'verde': '🟢 Estudo', 'azul': '🔵 Geral', 'roxo': '🟣 Lazer', 'cinza': '⚫ Outro',
}

PALAVRAS_CORES = {
    'vermelho': ['pessoal', 'academia', 'treino', 'médico', 'medico', 'dentista'],
    'laranja': ['reunião', 'reuniao', 'call', 'trabalho', 'escritório', 'cliente'],
    'verde': ['estudo', 'curso', 'aula', 'faculdade', 'escola'],
    'roxo': ['lazer', 'cinema', 'show', 'festa', 'aniversário'],
    'amarelo': ['importante', 'urgente', 'deadline', 'prazo'],
}

user_sessions = {}

def get_calendar_service():
    try:
        # Tentar carregar de variável de ambiente primeiro
        service_account_json = os.environ.get('GOOGLE_SERVICE_ACCOUNT')
        
        if service_account_json:
            print("🔍 Usando variável de ambiente")
            service_account_info = json.loads(service_account_json)
            credentials = service_account.Credentials.from_service_account_info(
                service_account_info, scopes=SCOPES)
        elif os.path.exists(SERVICE_ACCOUNT_FILE):
            print("🔍 Usando arquivo")
            credentials = service_account.Credentials.from_service_account_file(
                SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        else:
            print("❌ Nenhuma credencial encontrada")
            return None
            
        print("✅ Credenciais carregadas")
        return build('calendar', 'v3', credentials=credentials)
        
    except Exception as e:
        print(f"❌ Erro: {str(e)}")
        return None

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
    
    titulo = mensagem
    titulo = re.sub(r'\d{1,2}[\/\-\.]\d{1,2}', '', titulo)
    titulo = re.sub(r'\d{1,2}[:h]\d{2}', '', titulo)
    for cor in CORES.keys():
        titulo = re.sub(r'\b' + cor + r'\b', '', titulo, flags=re.IGNORECASE)
    titulo = re.sub(r'\b(agendar|marcar|lembre|me|de|as|às|dia)\b', '', titulo, flags=re.IGNORECASE)
    titulo = titulo.strip()
    info['titulo'] = titulo.title() if len(titulo) > 2 else "Evento"
    
    return info

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
        
        cor_nome = [v for k, v in CORES_NOMES.items() if CORES[k] == info['cor']][0]
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
    
    if any(x in msg_lower for x in ['ajuda', 'help', 'oi', 'olá']):
        return xml_response(
            "👋 *Assistente de Agenda*\n\n"
            "Como usar:\n"
            "• `reunião amanhã 15h` - Criar evento\n"
            "• `me lembre de pagar conta dia 25` - Criar lembrete\n\n"
            "Cores: vermelho, laranja, amarelo, verde, azul, roxo"
        )
    
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
    
    return xml_response(
        "🤔 Não entendi!\n\n"
        "Tente:\n"
        "• `reunião amanhã 15h`\n"
        "• `ajuda`"
    )

@app.route('/health', methods=['GET'])
def health():
    return {"status": "ok", "service": "whatsapp-calendar-bot"}

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
