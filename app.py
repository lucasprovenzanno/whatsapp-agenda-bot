import os
import re
import json
import pickle
from datetime import datetime, timedelta
from flask import Flask, request

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

app = Flask(__name__)

SCOPES = ['https://www.googleapis.com/auth/calendar']
CALENDAR_ID = 'primary'
TOKEN_FILE = 'token.pickle'
LEMBRETES_FILE = 'lembretes.json'

# Cores do Google Calendar (ID das cores)
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
    'vermelho': ['pessoal', 'academia', 'treino', 'médico', 'medico', 'dentista', 'consulta',
                 'exame', 'vacina', 'particular', 'família', 'familia', 'pessoal'],
    'laranja': ['reunião', 'reuniao', 'call', 'video', 'conferência', 'conferencia',
                'trabalho', 'escritório', 'escritorio', 'cliente', 'negócio', 'negocio'],
    'verde': ['estudo', 'curso', 'aula', 'aprender', 'treinamento', 'workshop',
              'palestra', 'seminário', 'seminario', 'certificação', 'certificacao'],
    'roxo': ['lazer', 'cinema', 'show', 'festa', 'aniversário', 'aniversario',
             'churrasco', 'jantar', 'almoço', 'almoco', 'happy hour', 'bar'],
    'amarelo': ['importante', 'urgente', 'prioridade', 'crítico', 'critico',
                'deadline', 'prazo', 'entrega', 'prova', 'exame final'],
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
        'segunda': 0, 'seg': 0, 'terça': 1, 'terca': 1, 'ter': 1,
        'quarta': 2, 'qua': 2, 'quinta': 3, 'qui': 3,
        'sexta': 4, 'sex': 4, 'sábado': 5, 'sabado': 5, 'sab': 5,
        'domingo': 6, 'dom': 6
    }

    for dia_nome, dia_num in dias_semana.items():
        if dia_nome in text_lower:
            dias_ahead = dia_num - base_date.weekday()
            if dias_ahead <= 0:
                dias_ahead += 7
            return (base_date + timedelta(days=dias_ahead)).replace(hour=0, minute=0, second=0, microsecond=0)

    padroes_data = [
        r'(?:dia\s+)?(\d{1,2})[\/\-\.](\d{1,2})(?:[\/\-\.](\d{2,4}))?',
    ]

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
    padroes = [
        r'(?:às\s+|as\s+)?(\d{1,2})[:h](\d{2})',
        r'(\d{1,2}):(\d{2})',
    ]

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
    if any(x in text_lower for x in ['meia hora', '30 min']):
        return 30
    if any(x in text_lower for x in ['hora e meia', '1h30']):
        return 90
    if any(x in text_lower for x in ['duas horas', '2h']):
        return 120
    if any(x in text_lower for x in ['uma hora', '1h']):
        return 60
    match = re.search(r'(\d+)\s*(?:min)', text_lower)
    if match:
        return int(match.group(1))
    match = re.search(r'(\d+)\s*(?:h)', text_lower)
    if match:
        return int(match.group(1)) * 60
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
        'descricao': ''
    }
    
    info['data'] = parse_date(mensagem)
    info['hora'] = parse_time(mensagem)
    if not is_lembrete:
        info['duracao'] = parse_duration(mensagem)
    
    info['titulo'] = extract_title(mensagem, is_lembrete)
    info['cor'] = detectar_cor(mensagem, info['titulo'])
    
    return info

def extract_title(mensagem, is_lembrete=False):
    msg_lower = mensagem.lower()
    if is_lembrete:
        prefixos = [r'me\s+(?:lembra|lembre)(?:\s+de)?\s+', r'lembrar\s+(?:de\s+)?']
        titulo = mensagem
        for prefixo in prefixos:
            titulo = re.sub(prefixo, '', titulo, flags=re.IGNORECASE)
    else:
        titulo = mensagem
        titulo = re.sub(r'(?:dia\s+)?\d{1,2}[\/\-\.]\d{1,2}(?:[\/\-\.]\d{2,4})?', '', titulo)
        titulo = re.sub(r'(?:às\s+|as\s+)?\d{1,2}[:h]\d{2}', '', titulo)
        for cor in CORES.keys():
            titulo = re.sub(r'\b' + cor + r'\b', '', titulo, flags=re.IGNORECASE)
    
    titulo = titulo.strip()
    titulo = re.sub(r'\s+', ' ', titulo)
    
    if not titulo or len(titulo) < 3:
        return "Evento"
    
    return titulo.strip().title()

def criar_evento(info):
    try:
        service = get_calendar_service()
        if not service:
            return None, "❌ Erro de autenticação"
        
        date_str = info['data'].strftime('%Y-%m-%d')
        start = datetime.strptime(f"{date_str} {info['hora']}", "%Y-%m-%d %H:%M")
        end = start + timedelta(minutes=info['duracao'])
        
        event = {
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
        
        event = service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
        
        cor_nome = get_cor_nome(info['cor'])
        data_fmt = start.strftime('%d/%m às %H:%M')
        
        return True, (f"✅ *Evento criado!*\n"
                     f"{cor_nome}\n"
                     f"📝 {info['titulo']}\n"
                     f"📅 {data_fmt}\n"
                     f"⏱️ {info['duracao']}min")
    except Exception as e:
        return None, f"❌ Erro: {str(e)}"

def criar_lembrete(info, telefone):
    lembrete = {
        'titulo': info['titulo'],
        'data': info['data'].strftime('%Y-%m-%d'),
        'hora': info['hora'],
        'telefone': telefone,
        'criado_em': datetime.now().isoformat()
    }
    
    lembretes = load_lembretes()
    lembretes.append(lembrete)
    save_lembretes(lembretes)
    
    data_hora = datetime.strptime(f"{lembrete['data']} {lembrete['hora']}", "%Y-%m-%d %H:%M")
    data_fmt = data_hora.strftime('%d/%m às %H:%M')
    
    return f"🔔 *Lembrete salvo!*\n📝 {info['titulo']}\n📅 {data_fmt}"

def get_resumo(periodo="semana"):
    try:
        service = get_calendar_service()
        if not service:
            return "❌ Erro de conexão"
        
        now = datetime.now()
        
        if periodo == "hoje":
            start = now.replace(hour=0, minute=0)
            end = start + timedelta(days=1)
            titulo = "*Hoje*"
        elif periodo == "amanha":
            start = (now + timedelta(days=1)).replace(hour=0, minute=0)
            end = start + timedelta(days=1)
            titulo = "*Amanhã*"
        else:
            start = now
            end = now + timedelta(days=7)
            titulo = "*Esta semana*"
        
        events_result = service.events().list(
            calendarId=CALENDAR_ID,
            timeMin=start.isoformat() + 'Z',
            timeMax=end.isoformat() + 'Z',
            maxResults=20,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        events = events_result.get('items', [])
        
        if not events:
            return f"📭 {titulo.replace('*', '')} sua agenda está livre!"
        
        resposta = f"📅 {titulo}\n\n"
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            if 'T' in start:
                dt = datetime.fromisoformat(start.replace('Z', '+00:00'))
                data_str = dt.strftime('%a %d/%m %H:%M')
            else:
                data_str = start
            
            cor_id = event.get('colorId', '1')
            cor_emoji = {'11': '🔴', '6': '🟠', '5': '🟡', '2': '🟢', '1': '🔵', '3': '🟣', '8': '⚫'}.get(cor_id, '')
            
            resposta += f"{cor_emoji} {data_str} - {event['summary']}\n"
        
        return resposta
    except Exception as e:
        return f"❌ Erro: {str(e)}"

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
    if any(x in msg_lower for x in ['o que tenho hoje', 'agenda hoje']):
        return xml_response(get_resumo("hoje"))
    
    if any(x in msg_lower for x in ['o que tenho amanhã', 'agenda amanhã']):
        return xml_response(get_resumo("amanha"))
    
    if any(x in msg_lower for x in ['semana', 'próximos dias']):
        return xml_response(get_resumo("semana"))
    
    if any(x in msg_lower for x in ['ajuda', 'help', 'cores']):
        return xml_response(
            "🎨 *Cores disponíveis:*\n\n"
            "🔴 Vermelho - Pessoal\n"
            "🟠 Laranja - Reuniões\n"
            "🟡 Amarelo - Importante\n"
            "🟢 Verde - Estudos\n"
            "🔵 Azul - Geral\n"
            "🟣 Roxo - Lazer\n\n"
            "Exemplo: 'reunião laranja sexta 15h'"
        )
    
    # Fluxo de confirmação
    if sessao['estado'] == 'confirmando':
        if any(x in msg_lower for x in ['sim', 'certo', 'ok', 'pode']):
            dados = sessao['dados']
            
            if dados['tipo'] == 'lembrete':
                resposta = criar_lembrete(dados, telefone)
            else:
                ok, resposta = criar_evento(dados)
            
            sessao['estado'] = 'livre'
            sessao['dados'] = {}
            return xml_response(resposta)
        
        elif any(x in msg_lower for x in ['não', 'nao', 'errado']):
            sessao['estado'] = 'livre'
            sessao['dados'] = {}
            return xml_response("❌ Cancelado. Vamos de novo!")
        
        else:
            for cor in CORES.keys():
                if cor in msg_lower:
                    sessao['dados']['cor'] = CORES[cor]
                    dados = sessao['dados']
                    cor_nome = get_cor_nome(dados['cor'])
                    data_fmt = dados['data'].strftime('%d/%m/%Y')
                    
                    return xml_response(
                        f"{cor_nome}\n"
                        f"📝 {dados['titulo']}\n"
                        f"📅 {data_fmt} às {dados['hora']}\n\n"
                        f"✅ Confirma? *sim* ou *não*?"
                    )
            
            return xml_response("🤔 Responde *sim* pra confirmar, *não* pra cancelar!")
    
    # Extrair info e iniciar confirmação
    info = extract_info(mensagem)
    
    if info['titulo'] and (info['data'] or info['hora'] or
                          any(x in msg_lower for x in ['marcar', 'agendar', 'lembra'])):
        faltando = []
        if not info['data']:
            faltando.append("data")
        if not info['hora']:
            faltando.append("horário")
        if faltando:
            return xml_response(f"📅 Quase! Preciso da {' e da '.join(faltando)}. Quando?")
        
        tipo_emoji = "🔔" if info['tipo'] == 'lembrete' else "📅"
        tipo_texto = "Lembrete" if info['tipo'] == 'lembrete' else "Evento"
        cor_nome = get_cor_nome(info['cor'])
        data_fmt = info['data'].strftime('%d/%m/%Y')
        
        resumo = (f"{tipo_emoji} *{tipo_texto}:*\n"
                 f"{cor_nome}\n"
                 f"📝 {info['titulo']}\n"
                 f"📅 {data_fmt} às {info['hora']}")
        
        if info['tipo'] == 'evento':
            resumo += f"\n⏱️ {info['duracao']}min"
        
        resumo += "\n\n✅ Tá certo? Responde *sim* ou me fala a cor!"
        
        sessao['estado'] = 'confirmando'
        sessao['dados'] = info
        return xml_response(resumo)
    
    # Resposta padrão
    return xml_response(
        "👋 Olá! Não entendi... 😅\n\n"
        "Tenta assim:\n"
        "• 'academia amanhã 6h'\n"
        "• 'reunião laranja sexta 15h'\n"
        "• 'me lembra de pagar conta dia 25'\n\n"
        "Manda *ajuda* pra ver as cores! 🎨"
    )

def xml_response(texto):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Message>{texto}</Message>
</Response>""", 200, {'Content-Type': 'application/xml'}

@app.route('/health', methods=['GET'])
def health():
    return {"status": "ok", "service": "whatsapp-calendar-bot"}

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
