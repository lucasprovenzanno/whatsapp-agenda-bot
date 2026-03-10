import os
import re
import json
import pickle
from datetime import datetime, timedelta
from flask import Flask, request, Response

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

app = Flask(__name__)

SCOPES = ['https://www.googleapis.com/auth/calendar']
CALENDAR_ID = 'primary'
TOKEN_FILE = 'token.pickle'
LEMBRETES_FILE = 'lembretes.json'

CORES = {
    'vermelho': '11',
    'laranja': '6',
    'amarelo': '5',
    'verde': '2',
    'azul': '1',
    'roxo': '3',
    'cinza': '8',
}

CORES_NOMES = {
    'vermelho': '🔴 Pessoal',
    'laranja': '🟠 Reunião',
    'amarelo': '🟡 Importante',
    'verde': '🟢 Estudo',
    'azul': '🔵 Geral',
    'roxo': '🟣 Lazer',
    'cinza': '⚫ Outro',
}

PALAVRAS_CORES = {
    'vermelho': ['pessoal', 'academia', 'treino', 'médico', 'medico', 'dentista', 'consulta'],
    'laranja': ['reunião', 'reuniao', 'call', 'video', 'trabalho', 'escritório', 'cliente'],
    'verde': ['estudo', 'curso', 'aula', 'aprender', 'faculdade', 'escola'],
    'roxo': ['lazer', 'cinema', 'show', 'festa', 'aniversário', 'churrasco'],
    'amarelo': ['importante', 'urgente', 'prioridade', 'deadline', 'prazo'],
}

user_sessions = {}

def load_lembretes():
    if os.path.exists(LEMBRETES_FILE):
        with open(LEMBRETES_FILE, 'r') as f:
            return json.load(f)
    return []

def save_lembretes(lembretes):
    with open(LEMBRETES_FILE, 'w') as f:
        json.dump(lembretes, f)

def get_calendar_service():
    creds = None
    
    # Tentar carregar de variável de ambiente primeiro
    if os.environ.get('GOOGLE_CREDENTIALS'):
        import json
        creds_info = json.loads(os.environ.get('GOOGLE_CREDENTIALS'))
        # Aqui precisamos de um token válido, não só as credenciais
        # Por enquanto retorna None para não quebrar
        return None
    
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, 'rb') as token:
            creds = pickle.load(token)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            return None

    return build('calendar', 'v3', credentials=creds)

def parse_date(text, base_date=None):
    if base_date is None:
        base_date = datetime.now()

    text_lower = text.lower().strip()

    if text_lower in ['hoje']:
        return base_date.replace(hour=0, minute=0, second=0, microsecond=0)

    if text_lower in ['amanhã', 'amanha']:
        return (base_date + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

    dias_semana = {
        'segunda': 0, 'terça': 1, 'quarta': 2, 'quinta': 3, 'sexta': 4, 'sábado': 5, 'domingo': 6
    }

    for dia_nome, dia_num in dias_semana.items():
        if dia_nome in text_lower:
            dias_ahead = dia_num - base_date.weekday()
            if dias_ahead <= 0:
                dias_ahead += 7
            return (base_date + timedelta(days=dias_ahead)).replace(hour=0, minute=0, second=0, microsecond=0)

    padroes_data = [r'(\d{1,2})[\/\-\.](\d{1,2})(?:[\/\-\.](\d{2,4}))?']

    for padrao in padroes_data:
        match = re.search(padrao, text_lower)
        if match:
            dia = int(match.group(1))
            mes = int(match.group(2))
            ano = int(match.group(3)) if match.group(3) else base_date.year
            if ano < 100:
                ano += 2000
            try:
                return datetime(ano, mes, dia)
            except:
                pass

    return None

def parse_time(text):
    text_lower = text.lower()
    padroes = [r'(\d{1,2})[:h](\d{2})', r'(\d{1,2})\s*h']

    for padrao in padroes:
        match = re.search(padrao, text_lower)
        if match:
            hora = int(match.group(1))
            minuto = int(match.group(2)) if match.group(2) else 0
            if 0 <= hora <= 23 and 0 <= minuto <= 59:
                return f"{hora:02d}:{minuto:02d}"
    return None

def parse_duration(text):
    text_lower = text.lower()
    if 'meia hora' in text_lower or '30 min' in text_lower:
        return 30
    if 'hora e meia' in text_lower or '1h30' in text_lower:
        return 90
    if 'duas horas' in text_lower or '2h' in text_lower:
        return 120
    if 'uma hora' in text_lower or '1h' in text_lower:
        return 60
    return 60

def detectar_cor(mensagem, titulo=None):
    texto = (mensagem + ' ' + (titulo or '')).lower()
    for cor, id_cor in CORES.items():
        if cor in texto:
            return id_cor
    for cor, palavras in PALAVRAS_CORES.items():
        for palavra in palavras:
            if palavra in texto:
                return CORES[cor]
    return CORES['azul']

def get_cor_nome(cor_id):
    for nome, id_c in CORES.items():
        if id_c == cor_id:
            return CORES_NOMES[nome]
    return CORES_NOMES['azul']

def extract_title(mensagem, is_lembrete=False):
    msg_lower = mensagem.lower()
    if is_lembrete:
        titulo = re.sub(r'me\s+(?:lembra|lembre)(?:\s+de)?\s+', '', msg_lower, flags=re.IGNORECASE)
    else:
        titulo = mensagem
        titulo = re.sub(r'\d{1,2}[\/\-\.]\d{1,2}', '', titulo)
        titulo = re.sub(r'\d{1,2}[:h]\d{2}', '', titulo)
    
    titulo = titulo.strip()
    if len(titulo) < 3:
        return "Evento"
    return titulo.strip().title()

def extract_info(mensagem):
    msg_lower = mensagem.lower()
    is_lembrete = any(x in msg_lower for x in ['me lembra', 'me lembre', 'lembrar de'])
    
    info = {
        'tipo': 'lembrete' if is_lembrete else 'evento',
        'titulo': None,
        'data': None,
        'hora': None,
        'duracao': 60,
        'cor': CORES['azul'],
    }
    
    info['data'] = parse_date(mensagem)
    info['hora'] = parse_time(mensagem)
    if not is_lembrete:
        info['duracao'] = parse_duration(mensagem)
    
    info['titulo'] = extract_title(mensagem, is_lembrete)
    info['cor'] = detectar_cor(mensagem, info['titulo'])
    
    return info

def xml_response(texto):
    # CORREÇÃO: XML limpo, sem espaços, com content-type correto
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
    
    # Comandos diretos
    if any(x in msg_lower for x in ['ajuda', 'help', 'oi', 'olá', 'ola']):
        return xml_response(
            "👋 Olá! Sou seu assistente de agenda!\n\n"
            "Como usar:\n"
            "• 'reunião amanhã 15h'\n"
            "• 'academia segunda 6h'\n"
            "• 'me lembre de pagar conta dia 25'\n\n"
            "Cores: vermelho, laranja, amarelo, verde, azul, roxo"
        )
    
    if any(x in msg_lower for x in ['o que tenho hoje', 'agenda hoje']):
        return xml_response("📅 Hoje sua agenda está livre!")
    
    if any(x in msg_lower for x in ['o que tenho amanhã', 'agenda amanhã']):
        return xml_response("📅 Amanhã sua agenda está livre!")
    
    # Extrair info e confirmar
    info = extract_info(mensagem)
    
    if info['titulo'] and (info['data'] or info['hora'] or
                          any(x in msg_lower for x in ['marcar', 'agendar', 'lembra', 'bota'])):
        if not info['data'] or not info['hora']:
            return xml_response("📅 Quase! Preciso da data e horário. Quando?")
        
        tipo_emoji = "🔔" if info['tipo'] == 'lembrete' else "📅"
        tipo_texto = "Lembrete" if info['tipo'] == 'lembrete' else "Evento"
        cor_nome = get_cor_nome(info['cor'])
        data_fmt = info['data'].strftime('%d/%m/%Y')
        
        resumo = f"{tipo_emoji} *{tipo_texto}:*\n{cor_nome}\n📝 {info['titulo']}\n📅 {data_fmt} às {info['hora']}"
        
        if info['tipo'] == 'evento':
            resumo += f"\n⏱️ {info['duracao']}min"
        
        resumo += "\n\n✅ Tá certo? Responde *sim*"
        
        sessao['estado'] = 'confirmando'
        sessao['dados'] = info
        return xml_response(resumo)
    
    # Fluxo de confirmação
    if sessao['estado'] == 'confirmando':
        if any(x in msg_lower for x in ['sim', 'certo', 'ok']):
            # Aqui criaria o evento, mas por enquanto só confirma
            sessao['estado'] = 'livre'
            dados = sessao['dados']
            return xml_response(f"✅ {dados['titulo']} confirmado para {dados['data'].strftime('%d/%m')} às {dados['hora']}!")
        
        elif any(x in msg_lower for x in ['não', 'nao', 'cancelar']):
            sessao['estado'] = 'livre'
            return xml_response("❌ Cancelado. Pode enviar outro!")
        
        else:
            return xml_response("🤔 Responde *sim* para confirmar ou *não* para cancelar!")
    
    # Padrão
    return xml_response(
        "👋 Não entendi! 😅\n\n"
        "Tenta:\n"
        "• 'reunião amanhã 15h'\n"
        "• 'academia segunda 6h'\n"
        "• 'me lembre de pagar conta dia 25'\n\n"
        "Manda *ajuda* para mais opções! 🎨"
    )

@app.route('/health', methods=['GET'])
def health():
    return {"status": "ok", "service": "whatsapp-calendar-bot"}

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
