import os, json, re
from datetime import datetime, timedelta
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from google.oauth2 import service_account
from googleapiclient.discovery import build

app = Flask(__name__)
CAL_ID = 'lucasprovenzano.cobeb@gmail.com'

# Auth
creds = service_account.Credentials.from_service_account_info(
    json.loads(os.environ['GOOGLE_CREDENTIALS_JSON']),
    scopes=['https://www.googleapis.com/auth/calendar']
)
service = build('calendar', 'v3', credentials=creds)

def parse(msg):
    hoje = datetime.now()
    msg = msg.lower()
    
    # Data
    if 'amanhã' in msg: data = hoje + timedelta(days=1)
    elif 'hoje' in msg: data = hoje
    elif m := re.search(r'segunda|terça|quarta|quinta|sexta|sábado|domingo', msg):
        dias = {'segunda':0,'terça':1,'quarta':2,'quinta':3,'sexta':4,'sábado':5,'domingo':6}
        ate = (dias[m.group()] - hoje.weekday()) % 7 or 7
        data = hoje + timedelta(days=ate)
    elif m := re.search(r'(\d{1,2})(?:/(\d{1,2}))?', msg):
        dia, mes = int(m.group(1)), int(m.group(2)) if m.group(2) else hoje.month
        data = datetime(hoje.year, mes, dia)
        if data < hoje: data = datetime(hoje.year+1, mes, dia)
    else: return None
    
    # Hora
    hora, minuto = 9, 0
    if m := re.search(r'(\d{1,2})[:h]?(\d{2})?', msg):
        hora, minuto = int(m.group(1)), int(m.group(2) or 0)
        if 'tarde' in msg and hora < 12: hora += 12
    
    inicio = data.replace(hour=hora, minute=minuto)
    if inicio < hoje: inicio += timedelta(days=1)
    
    # Título
    titulo = re.sub(r'(\d{1,2})[:h]?(\d{2})?|amanh[ãa]|hoje|dia|da\s*(manhã|tarde|noite)', '', msg)
    titulo = re.sub(r'\s+', ' ', titulo).strip().title() or "Evento"
    
    return titulo, inicio, inicio + timedelta(hours=1)

@app.route("/webhook", methods=['POST'])
def webhook():
    msg = request.values.get('Body', '').strip()
    resp = MessagingResponse()
    
    if msg.lower() == 'ajuda':
        resp.message("🤖 Comandos: reunião amanhã 15h, ajuda")
        return str(resp)
    
    if not (p := parse(msg)):
        resp.message("❓ Ex: reunião amanhã 15h")
        return str(resp)
    
    titulo, inicio, fim = p
    evento = {
        'summary': titulo,
        'start': {'dateTime': inicio.isoformat(), 'timeZone': 'America/Sao_Paulo'},
        'end': {'dateTime': fim.isoformat(), 'timeZone': 'America/Sao_Paulo'}
    }
    
    try:
        ev = service.events().insert(calendarId=CAL_ID, body=evento).execute()
        resp.message(f"✅ {titulo}\n📅 {inicio.strftime('%d/%m %H:%M')}\n🔗 {ev.get('htmlLink')}")
    except Exception as e:
        resp.message(f"❌ Erro: {str(e)[:100]}")
    
    return str(resp)

@app.route("/")
def health():
    return "OK"

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
