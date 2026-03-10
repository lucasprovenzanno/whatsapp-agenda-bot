from flask import Flask, request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from datetime import datetime, timedelta
import pickle
import os
import re
import json

app = Flask(__name__)

SCOPES = ['https://www.googleapis.com/auth/calendar']
CALENDAR_ID = 'primary'
TOKEN_FILE = 'token.pickle'
LEMBRETES_FILE = 'lembretes.json'

# Cores do Google Calendar (ID das cores)
CORES = {
    'vermelho': '11',      # Tomato
    'laranja': '6',        # Pumpkin
    'amarelo': '5',        # Banana
    'verde': '2',          # Sage
    'azul': '1',           # Peacock (padrão)
    'roxo': '3',           # Grape
    'cinza': '8',          # Graphite
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

# Mapeamento inteligente de palavras para cores
PALAVRAS_CORES = {
    'vermelho': ['pessoal', 'academia', 'treino', 'médico', 'medico', 'dentista', 'consulta', 
                 'exame', 'vacina', 'particular', 'família', 'familia', 'pessoal'],
    'laranja': ['reunião', 'reuniao', 'call', 'video', 'conferência', 'conferencia', 
                'trabalho', 'escritório', 'escritorio', 'cliente', 'negócio', 'negocio',
                'reunião de', 'reuniao de', 'call com', 'reunião com'],
    'verde': ['estudo', 'curso', 'aula', 'aprender', 'treinamento', 'workshop', 
              'palestra', 'seminário', 'seminario', 'certificação', 'certificacao',
              'faculdade', 'escola', 'estudar'],
    'roxo': ['lazer', 'cinema', 'show', 'festa', 'aniversário', 'aniversario', 
             'churrasco', 'jantar', 'almoço', 'almoco', 'happy hour', 'bar',
             'viagem', 'passeio', 'parque', 'praia', 'descanso', 'folga'],
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

# ============ FUNÇÕES DE DATA/TEMPO ============

def parse_date(text, base_date=None):
    if base_date is None:
        base_date = datetime.now()
    
    text_lower = text.lower().strip()
    
    if text_lower in ['hoje']:
        return base_date.replace(hour=0, minute=0, second=0, microsecond=0)
    
    if text_lower in ['amanhã', 'amanha']:
        return (base_date + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    
    if any(x in text_lower for x in ['depois de amanhã', 'depois de amanha']):
        return (base_date + timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)
    
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
        r'(\d{1,2})\s+de\s+([a-zç]+)(?:\s+de\s+(\d{2,4}))?'
    ]
    
    for padrao in padroes_data:
        match = re.search(padrao, text_lower)
        if match:
            if 'de' in text_lower and match.group(2).isalpha():
                dia = int(match.group(1))
                mes_nome = match.group(2).lower()
                meses = {
                    'janeiro': 1, 'jan': 1, 'fevereiro': 2, 'fev': 2, 'março': 3, 'marco': 3, 'mar': 3,
                    'abril': 4, 'abr': 4, 'maio': 5, 'junho': 6, 'jun': 6,
                    'julho': 7, 'jul': 7, 'agosto': 8, 'ago': 8,
                    'setembro': 9, 'set': 9, 'outubro': 10, 'out': 10,
                    'novembro': 11, 'nov': 11, 'dezembro': 12, 'dez': 12
                }
                mes = meses.get(mes_nome, base_date.month)
                ano = int(match.group(3)) if match.group(3) else base_date.year
            else:
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
        r'(?:às\s+|as\s+)?(\d{1,2})[:h](\d{2})(?:min|h)?',
        r'(?:às\s+|as\s+)?(\d{1,2})\s*(?:horas?|h)(?:\s+e\s+(\d{2})\s*(?:min|minutos?))?',
        r'(\d{1,2}):(\d{2})(?:\s*h)?',
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
    
    if any(x in text_lower for x in ['meia hora', '30 min', 'trinta min']):
        return 30
    
    if any(x in text_lower for x in ['hora e meia', '1h30', '1:30', '1 h 30', 'uma hora e meia']):
        return 90
    
    if any(x in text_lower for x in ['duas horas', '2h', '2 horas', '2 h']):
        return 120
    
    if any(x in text_lower for x in ['três horas', '3h', '3 horas', '3 h']):
        return 180
    
    if any(x in text_lower for x in ['uma hora', '1h', '1 hora', '1 h', 'uma h']):
        return 60
    
    match = re.search(r'(\d+)\s*(?:min|minutos?)', text_lower)
    if match:
        return int(match.group(1))
    
    match = re.search(r'(\d+)\s*(?:h|horas?)', text_lower)
    if match:
        return int(match.group(1)) * 60
    
    return 60

# ============ DETECTAR COR ============

def detectar_cor(mensagem, titulo=None):
    """Detecta a cor baseado no conteúdo da mensagem"""
    texto = (mensagem + ' ' + (titulo or '')).lower()
    
    # Primeiro verifica se usuário especificou cor explicitamente
    for cor, id_cor in CORES.items():
        if cor in texto:
            return id_cor
    
    # Depois verifica por palavras-chave
    for cor, palavras in PALAVRAS_CORES.items():
        for palavra in palavras:
            if palavra in texto:
                return CORES[cor]
    
    # Padrão: azul
    return CORES['azul']

def get_cor_nome(cor_id):
    """Retorna nome da cor pelo ID"""
    for nome, id_c in CORES.items():
        if id_c == cor_id:
            return CORES_NOMES[nome]
    return CORES_NOMES['azul']

# ============ EXTRAÇÃO DE INFO ============

def extract_info(mensagem):
    msg_lower = mensagem.lower()
    
    is_lembrete = any(x in msg_lower for x in [
        'me lembra', 'me lembre', 'lembrar de', 'lembrar que', 'não esquecer',
        'avisar quando', 'avisar dia', 'notificar'
    ])
    
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
    
    titulo = extract_title(mensagem, is_lembrete)
    info['titulo'] = titulo
    
    # Detectar cor
    info['cor'] = detectar_cor(mensagem, titulo)
    
    return info

def extract_title(mensagem, is_lembrete=False):
    msg_lower = mensagem.lower()
    
    if is_lembrete:
        prefixos = [
            r'me\s+(?:lembra|lembre)(?:\s+de)?\s+',
            r'lembrar\s+(?:de\s+)?',
            r'não\s+esquecer\s+(?:de\s+)?',
            r'avisar\s+(?:quando|dia)?\s*',
        ]
        titulo = mensagem
        for prefixo in prefixos:
            titulo = re.sub(prefixo, '', titulo, flags=re.IGNORECASE)
    else:
        titulo = mensagem
        
        # Remover data/hora/duração/cor
        titulo = re.sub(r'(?:dia\s+)?\d{1,2}[\/\-\.]\d{1,2}(?:[\/\-\.]\d{2,4})?', '', titulo)
        titulo = re.sub(r'\d{1,2}\s+de\s+[a-zç]+(?:\s+de\s+\d{4})?', '', titulo, flags=re.IGNORECASE)
        titulo = re.sub(r'(?:às\s+|as\s+)?\d{1,2}[:h]\d{2}(?:min|h)?', '', titulo)
        titulo = re.sub(r'\d{1,2}\s*(?:horas?|h)(?:\s+e\s+\d{2}\s*(?:min)?)?', '', titulo)
        titulo = re.sub(r'\d+\s*(?:min|minutos?|h|horas?)', '', titulo)
        
        # Remover cores explicitas
        for cor in CORES.keys():
            titulo = re.sub(r'\b' + cor + r'\b', '', titulo, flags=re.IGNORECASE)
        
        acoes = ['marcar', 'agendar', 'criar', 'bota', 'coloca', 'adiciona', 'novo', 
                'preciso', 'tenho', 'vou ter', 'quero', 'vamos']
        for acao in acoes:
            titulo = re.sub(r'\b' + acao + r'\b', '', titulo, flags=re.IGNORECASE)
        
        titulo = re.sub(r'^\s*(?:de|com|para|em|no|na)\s+', '', titulo, flags=re.IGNORECASE)
        titulo = re.sub(r'\s+(?:de|com|para|em|no|na)\s*$', '', titulo, flags=re.IGNORECASE)
    
    titulo = titulo.strip()
    titulo = re.sub(r'\s+', ' ', titulo)
    
    if not titulo or len(titulo) < 3:
        palavras = mensagem.split()
        palavras_ignorar = ['o', 'a', 'os', 'as', 'um', 'uma', 'de', 'da', 'do', 'em', 'no', 'na', 
                           'para', 'por', 'com', 'e', 'que', 'dia', 'às', 'as', 'me', 'de', 'cor']
        
        titulo_palavras = []
        for p in palavras:
            p_limpa = re.sub(r'[^\w\s]', '', p.lower())
            if p_limpa not in palavras_ignorar and not re.match(r'^\d', p):
                titulo_palavras.append(p)
        
        titulo = ' '.join(titulo_palavras[:4])
    
    return titulo.strip().title() if titulo else "Evento"

# ============ OPERAÇÕES ============

def criar_evento(info):
    try:
        service = get_calendar_service()
        if not service:
            return None, "❌ Erro de autenticação. Rode setup_auth.py"
        
        date_str = info['data'].strftime('%Y-%m-%d')
        start = datetime.strptime(f"{date_str} {info['hora']}", "%Y-%m-%d %H:%M")
        end = start + timedelta(minutes=info['duracao'])
        
        event = {
            'summary': info['titulo'],
            'description': info.get('descricao', ''),
            'colorId': info['cor'],  # AQUI ESTÁ A COR!
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
    
    return f"🔔 *Lembrete salvo!*\n📝 {info['titulo']}\n📅 {data_fmt}\n\nTe aviso na hora! 😉"

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
                data_str = f"{start[8:10]}/{start[5:7]}"
            
            # Mostrar cor se tiver
            cor_id = event.get('colorId', '1')
            cor_emoji = ''
            for nome, id_c in CORES.items():
                if id_c == cor_id:
                    cor_emoji = {'vermelho': '🔴', 'laranja': '🟠', 'amarelo': '🟡',
                                'verde': '🟢', 'azul': '🔵', 'roxo': '🟣', 'cinza': '⚫'}.get(nome, '')
                    break
            
            resposta += f"{cor_emoji} {data_str} - {event['summary']}\n"
        
        return resposta
        
    except Exception as e:
        return f"❌ Erro: {str(e)}"

def buscar_eventos(query):
    try:
        service = get_calendar_service()
        if not service:
            return "❌ Erro de conexão"
        
        now = datetime.now()
        start = (now - timedelta(days=180)).isoformat() + 'Z'
        end = (now + timedelta(days=180)).isoformat() + 'Z'
        
        events_result = service.events().list(
            calendarId=CALENDAR_ID,
            timeMin=start,
            timeMax=end,
            maxResults=50,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        events = events_result.get('items', [])
        query_lower = query.lower()
        
        matches = [e for e in events if query_lower in e['summary'].lower()]
        
        if not matches:
            return f"🔍 Nada encontrado com '{query}'"
        
        resposta = f"🔍 *{len(matches)} resultados:*\n\n"
        for evt in matches[-5:]:
            start = evt['start'].get('dateTime', evt['start'].get('date'))
            if 'T' in start:
                dt = datetime.fromisoformat(start.replace('Z', '+00:00'))
                data_str = dt.strftime('%d/%m/%Y %H:%M')
            else:
                data_str = start
            
            cor_id = evt.get('colorId', '1')
            cor_emoji = ''
            for nome, id_c in CORES.items():
                if id_c == cor_id:
                    cor_emoji = {'vermelho': '🔴', 'laranja': '🟠', 'amarelo': '🟡',
                                'verde': '🟢', 'azul': '🔵', 'roxo': '🟣', 'cinza': '⚫'}.get(nome, '')
                    break
            
            resposta += f"{cor_emoji} {data_str} - {evt['summary']}\n"
        
        return resposta
        
    except Exception as e:
        return f"❌ Erro: {str(e)}"

# ============ WEBHOOK ============

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
    
    if match := re.search(r'(?:quando foi|buscar|procurar)\s+(.+?)(?:\?|$)', msg_lower):
        return xml_response(buscar_eventos(match.group(1).strip()))
    
    if any(x in msg_lower for x in ['cancelar', 'deletar']):
        return xml_response("🗑️ Me fala: 'cancelar [nome do evento]'")
    
    if any(x in msg_lower for x in ['ajuda', 'help', 'cores']):
        return xml_response(
            "🎨 *Cores disponíveis:*\n\n"
            "🔴 *Vermelho* - Pessoal (academia, médico, dentista)\n"
            "🟠 *Laranja* - Reuniões de trabalho\n"
            "🟡 *Amarelo* - Importante/Urgente\n"
            "🟢 *Verde* - Estudos/Cursos\n"
            "🔵 *Azul* - Geral (padrão)\n"
            "🟣 *Roxo* - Lazer/Entretenimento\n\n"
            "*Como usar:*\n"
            "• 'academia amanhã 6h' → 🔴 Vermelho (automático)\n"
            "• 'reunião laranja sexta 15h' → 🟠 Laranja\n"
            "• 'dentista vermelho dia 20/03 10h' → 🔴 Vermelho"
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
        
        elif any(x in msg_lower for x in ['muda cor', 'troca cor', 'outra cor']):
            return xml_response(
                "🎨 Qual cor?\n"
                "🔴 Vermelho - Pessoal\n"
                "🟠 Laranja - Reunião\n"
                "🟡 Amarelo - Importante\n"
                "🟢 Verde - Estudo\n"
                "🔵 Azul - Geral\n"
                "🟣 Roxo - Lazer"
            )
        
        else:
            # Tentar detectar se usuário escolheu cor
            for cor in CORES.keys():
                if cor in msg_lower:
                    sessao['dados']['cor'] = CORES[cor]
                    dados = sessao['dados']
                    cor_nome = get_cor_nome(dados['cor'])
                    data_fmt = dados['data'].strftime('%d/%m/%Y')
                    
                    return xml_response(
                        f"{cor_nome}\n"
                        f"📝 {dados['titulo']}\n"
                        f"📅 {data_fmt} às {dados['hora']}\n"
                        f"⏱️ {dados['duracao']}min\n\n"
                        f"✅ Confirma? *sim* ou *não*?"
                    )
            
            return xml_response("🤔 Responde *sim* pra confirmar, *não* pra cancelar, ou me fala a cor!")
    
    # Extrair info
    info = extract_info(mensagem)
    
    if info['titulo'] and (info['data'] or info['hora'] or 
                          any(x in msg_lower for x in ['marcar', 'agendar', 'lembra', 'bota'])):
        
        faltando = []
        if not info['data']:
            faltando.append("data")
        if not info['hora']:
            faltando.append("horário")
        
        if faltando:
            return xml_response(f"📅 Quase! Preciso da {' e da '.join(faltando)}. Quando?")
        
        # Mostrar para confirmação
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
        
        resumo += "\n\n✅ Tá certo? Responde *sim* ou me fala a cor (vermelho, laranja, etc)"
        
        sessao['estado'] = 'confirmando'
        sessao['dados'] = info
        
        return xml_response(resumo)
    
    # Padrão
    return xml_response(
        "👋 E aí! Não entendi... 😅\n\n"
        "Tenta assim:\n"
        "• 'academia amanhã 6h' (🔴 vermelho automático)\n"
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