import os, json, re, threading, time
from datetime import datetime, timedelta
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
from google.oauth2 import service_account
from googleapiclient.discovery import build

app = Flask(__name__)
CAL_ID = 'lucasprovenzano.cobeb@gmail.com'

# Twilio (para enviar mensagens depois)
TWILIO_SID = os.environ.get('TWILIO_ACCOUNT_SID')
TWILIO_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN')
TWILIO_NUMBER = os.environ.get('TWILIO_PHONE_NUMBER')
twilio_client = Client(TWILIO_SID, TWILIO_TOKEN) if TWILIO_SID and TWILIO_TOKEN else None

# Auth Google
creds = service_account.Credentials.from_service_account_info(
    json.loads(os.environ['GOOGLE_CREDENTIALS_JSON']),
    scopes=['https://www.googleapis.com/auth/calendar']
)
service = build('calendar', 'v3', credentials=creds)

# Lembretes pendentes (em memória - reinicia se o servidor cair)
lembretes = []

def parse(msg):
    hoje = datetime.now()
    msg_lower = msg.lower()
    
    # Detecta se é lembrete (não vai para agenda)
    eh_lembrete = 'lembrete' in msg_lower
    
    # Detecta cor
    cores = {'vermelho': '11', 'laranja': '6', 'amarelo': '5', 'verde': '10', 'azul': '9', 'roxo': '3'}
    cor = '9'
    for nome, codigo in cores.items():
        if nome in msg_lower:
            cor = codigo
            msg_lower = msg_lower.replace(nome, '')
            break
    
    # Limpa
    msg_lower = re.sub(r'(lembrete|de\s+)', '', msg_lower)
    
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
    
    # Hora (se não for lembrete ou se tiver hora específica no lembrete)
    hora, minuto = 9, 0
    tem_hora = False
    
    padroes = [r'(?:às\s*)?(\d{1,2}):(\d{2})', r'(\d{1,2})h(\d{2})', r'(\d{1,2})\s*(?:h|horas?)', r'(?:às?\s+)(\d{1,2})']
    for padrao in padroes:
        if m := re.search(padrao, msg_lower):
            hora = int(m.group(1))
            if m.lastindex >= 2 and m.group(2): minuto = int(m.group(2))
            if 'tarde' in msg_lower and hora < 12: hora += 12
            elif 'noite' in msg_lower and hora < 12: hora += 12
            msg_lower = msg_lower.replace(m.group(0), '')
            tem_hora = True
            break
    
    # Monta datetime
    inicio = data.replace(hour=hora, minute=minuto, second=0)
    if inicio < hoje and not eh_lembrete:
        inicio += timedelta(days=1)
    
    # Título
    titulo = re.sub(r'\s+', ' ', msg_lower).strip().title()
    if len(titulo) < 2: titulo = "Lembrete" if eh_lembrete else "Evento"
    
    return titulo, inicio, eh_lembrete, cor

def enviar_lembrete(numero, mensagem, quando):
    """Agenda envio de mensagem no futuro"""
    agora = datetime.now()
    espera = (quando - agora).total_seconds()
    
    if espera <= 0:
        # Já passou, envia agora
        if twilio_client:
            twilio_client.messages.create(
                body=mensagem,
                from_=f'whatsapp:{TWILIO_NUMBER}',
                to=numero
            )
        return
    
    def enviar():
        time.sleep(espera)
        if twilio_client:
            try:
                twilio_client.messages.create(
                    body=mensagem,
                    from_=f'whatsapp:{TWILIO_NUMBER}',
                    to=numero
                )
                print(f"✅ Lembrete enviado: {mensagem[:30]}")
            except Exception as e:
                print(f"❌ Erro ao enviar lembrete: {e}")
    
    threading.Thread(target=enviar, daemon=True).start()

@app.route("/webhook", methods=['POST'])
def webhook():
    msg = request.values.get('Body', '').strip()
    numero = request.values.get('From', '')
    resp = MessagingResponse()
    
    if msg.lower() in ['ajuda', 'help']:
        resp.message("""🤖 *Bot de Agenda*

*Eventos (vão para Google Calendar):*
• reunião amanhã 15h
• médico segunda 14:30
• academia hoje 18h vermelho

*Lembretes (mensagem no WhatsApp, não vai para agenda):*
• lembrete pagar conta amanhã 15h
• lembrete reunião com João segunda 10h

*Cores:* vermelho, laranja, amarelo, verde, azul, roxo""")
        return str(resp)
    
    if not (p := parse(msg)):
        resp.message("❓ Não entendi. Envie 'ajuda' para ver exemplos.")
        return str(resp)
    
    titulo, inicio, eh_lembrete, cor = p
    
    if eh_lembrete:
        # Agenda mensagem para 15 minutos antes
        horario_lembrete = inicio - timedelta(minutes=15)
        
        mensagem_lembrete = f"⏰ *Lembrete:* {titulo}\n📅 {inicio.strftime('%d/%m/%Y %H:%M')}"
        
        enviar_lembrete(numero, mensagem_lembrete, horario_lembrete)
        
        emoji_cor = {'11': '🔴', '6': '🟠', '5': '🟡', '10': '🟢', '9': '🔵', '3': '🟣'}.get(cor, '🔵')
        resp.message(f"{emoji_cor} ⏰ *Lembrete agendado!*\n\n*{titulo}*\n📅 {inicio.strftime('%d/%m/%Y %H:%M')}\n\n💬 Vou te avisar às {horario_lembrete.strftime('%H:%M')}")
        
    else:
        # Cria evento no Google Calendar
        evento = {
            'summary': titulo,
            'colorId': cor,
            'start': {'dateTime': inicio.isoformat(), 'timeZone': 'America/Sao_Paulo'},
            'end': {'dateTime': (inicio + timedelta(hours=1)).isoformat(), 'timeZone': 'America/Sao_Paulo'},
            'reminders': {'useDefault': False, 'overrides': [{'method': 'popup', 'minutes': 30}]}
        }
        
        try:
            ev = service.events().insert(calendarId=CAL_ID, body=evento).execute()
            emoji_cor = {'11': '🔴', '6': '🟠', '5': '🟡', '10': '🟢', '9': '🔵', '3': '🟣'}.get(cor, '🔵')
            resp.message(f"{emoji_cor} 📅 *{titulo}*\n📆 {inicio.strftime('%d/%m/%Y %H:%M')}\n\n🔗 {ev.get('htmlLink')}")
        except Exception as e:
            resp.message(f"❌ Erro: {str(e)[:100]}")
    
    return str(resp)

@app.route("/")
def health():
    return "OK"

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
