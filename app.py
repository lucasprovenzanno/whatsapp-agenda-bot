import os, json, re
from datetime import datetime, timedelta
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from google.oauth2 import service_account
from googleapiclient.discovery import build

app = Flask(__name__)
CAL_ID = 'lucasprovenzano.cobeb@gmail.com'

# Cores disponíveis
CORES = {
    'vermelho': '11', 'laranja': '6', 'amarelo': '5',
    'verde': '10', 'azul': '9', 'roxo': '3'
}

# Auth
creds = service_account.Credentials.from_service_account_info(
    json.loads(os.environ['GOOGLE_CREDENTIALS_JSON']),
    scopes=['https://www.googleapis.com/auth/calendar']
)
service = build('calendar', 'v3', credentials=creds)

def parse(msg):
    hoje = datetime.now()
    msg_lower = msg.lower()
    
    # Detecta se é tarefa (tem "tarefa", "lembrete", "lembrar", "pagar", "conta")
    eh_tarefa = any(p in msg_lower for p in ['tarefa', 'lembrete', 'lembrar', 'pagar', 'conta', 'comprar', 'buscar'])
    
    # Detecta cor sugerida
    cor = '9'  # azul padrão
    for nome, codigo in CORES.items():
        if nome in msg_lower:
            cor = codigo
            msg_lower = msg_lower.replace(nome, '')  # remove do texto
            break
    
    # Remove palavras de controle
    msg_lower = re.sub(r'(tarefa|lembrete|lembrar|de\s+)', '', msg_lower)
    
    # Data
    if 'amanhã' in msg_lower or 'amanha' in msg_lower:
        data = hoje + timedelta(days=1)
        msg_lower = re.sub(r'amanh[ãa]', '', msg_lower)
    elif 'hoje' in msg_lower:
        data = hoje
        msg_lower = msg_lower.replace('hoje', '')
    elif m := re.search(r'segunda|terça|terca|quarta|quinta|sexta|sábado|sabado|domingo', msg_lower):
        dias = {'segunda':0,'terça':1,'terca':1,'quarta':2,'quinta':3,'sexta':4,'sábado':5,'sabado':5,'domingo':6}
        ate = (dias[m.group()] - hoje.weekday()) % 7 or 7
        data = hoje + timedelta(days=ate)
        msg_lower = msg_lower.replace(m.group(), '')
    elif m := re.search(r'(\d{1,2})(?:/(\d{1,2}))?', msg_lower):
        dia, mes = int(m.group(1)), int(m.group(2)) if m.group(2) else hoje.month
        try:
            data = datetime(hoje.year, mes, dia)
            if data < hoje.replace(hour=0, minute=0): data = datetime(hoje.year+1, mes, dia)
        except: return None
        msg_lower = msg_lower[:m.start()] + msg_lower[m.end():]
    else:
        return None
    
    # Hora (se não for tarefa)
    hora, minuto = None, 0
    if not eh_tarefa:
        # Procura padrões: 15h, 15:30, 15h30, 15 horas, às 15, etc
        padroes_hora = [
            r'(?:às\s*)?(\d{1,2}):(\d{2})',           # 15:30
            r'(\d{1,2})h(\d{2})',                       # 15h30
            r'(\d{1,2})\s*(?:h|horas?)(?:\s|$)',        # 15h ou 15 horas
            r'(?:às?\s+)(\d{1,2})(?:\s|$)',             # às 15
        ]
        
        for padrao in padroes_hora:
            if m := re.search(padrao, msg_lower):
                hora = int(m.group(1))
                if m.lastindex >= 2 and m.group(2):
                    minuto = int(m.group(2))
                # Ajusta período
                if 'tarde' in msg_lower and hora < 12: hora += 12
                elif 'noite' in msg_lower and hora < 12: hora += 12
                elif 'manhã' in msg_lower and hora > 12: hora = hora  # mantém
                msg_lower = msg_lower.replace(m.group(0), '')
                break
        
        # Se não achou hora, assume 9h
        if hora is None:
            hora = 9
    
    # Monta datetime
    if eh_tarefa or hora is None:
        # Tarefa: dia inteiro (sem hora)
        inicio = data.replace(hour=0, minute=0)
        fim = data.replace(hour=23, minute=59)
        tipo = 'tarefa'
    else:
        inicio = data.replace(hour=hora, minute=minuto, second=0)
        if inicio < hoje:
            inicio += timedelta(days=1)
        fim = inicio + timedelta(hours=1)
        tipo = 'evento'
    
    # Título
    titulo = re.sub(r'\s+', ' ', msg_lower).strip().title()
    if len(titulo) < 2: titulo = "Evento" if tipo == 'evento' else "Tarefa"
    
    return titulo, inicio, fim, tipo, cor

@app.route("/webhook", methods=['POST'])
def webhook():
    msg = request.values.get('Body', '').strip()
    resp = MessagingResponse()
    
    if msg.lower() in ['ajuda', 'help']:
        resp.message("""🤖 *Bot de Agenda*

*Eventos (com hora):*
• reunião amanhã 15h
• médico segunda 14:30
• academia hoje 18h

*Tarefas (dia inteiro):*
• tarefa pagar conta dia 25
• lembrete comprar leite amanhã
• pagar boleto sexta

*Cores (opcional):*
• vermelho, laranja, amarelo, verde, azul, roxo

*Exemplo com cor:*
• reunião importante amanhã 15h vermelho""")
        return str(resp)
    
    if not (p := parse(msg)):
        resp.message("❓ Não entendi. Envie 'ajuda' para ver exemplos.")
        return str(resp)
    
    titulo, inicio, fim, tipo, cor = p
    
    # Monta evento
    evento = {
        'summary': titulo,
        'colorId': cor,
    }
    
    if tipo == 'tarefa':
        # Tarefa: evento de dia inteiro
        evento['start'] = {'date': inicio.strftime('%Y-%m-%d')}
        evento['end'] = {'date': (inicio + timedelta(days=1)).strftime('%Y-%m-%d')}
        evento['description'] = 'Tarefa criada via WhatsApp'
    else:
        # Evento: com hora específica
        evento['start'] = {'dateTime': inicio.isoformat(), 'timeZone': 'America/Sao_Paulo'}
        evento['end'] = {'dateTime': fim.isoformat(), 'timeZone': 'America/Sao_Paulo'}
        evento['reminders'] = {'useDefault': False, 'overrides': [{'method': 'popup', 'minutes': 30}]}
    
    try:
        ev = service.events().insert(calendarId=CAL_ID, body=evento).execute()
        
        emoji_cor = {'11': '🔴', '6': '🟠', '5': '🟡', '10': '🟢', '9': '🔵', '3': '🟣'}.get(cor, '🔵')
        emoji_tipo = '📌' if tipo == 'tarefa' else '📅'
        
        if tipo == 'tarefa':
            resp.message(f"{emoji_cor} {emoji_tipo} *{titulo}*\n📆 {inicio.strftime('%d/%m/%Y')}\n\n🔗 {ev.get('htmlLink')}")
        else:
            resp.message(f"{emoji_cor} {emoji_tipo} *{titulo}*\n📆 {inicio.strftime('%d/%m/%Y %H:%M')}\n\n🔗 {ev.get('htmlLink')}")
    except Exception as e:
        resp.message(f"❌ Erro: {str(e)[:100]}")
    
    return str(resp)

@app.route("/")
def health():
    return "OK"

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
