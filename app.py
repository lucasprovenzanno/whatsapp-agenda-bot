import os, json, re, threading, time
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
from google.oauth2 import service_account
from googleapiclient.discovery import build

app = Flask(__name__)

CAL_ID_EVENTOS = 'lucasprovenzano.cobeb@gmail.com'
CAL_ID_LEMBRETES = 'lucas.provenzanno@gmail.com'

CORES = {'vermelho': '11', 'laranja': '6', 'amarelo': '5', 'verde': '10', 'azul': '9', 'roxo': '3'}

TWILIO_SID = os.environ.get('TWILIO_ACCOUNT_SID')
TWILIO_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN')
TWILIO_NUMBER = os.environ.get('TWILIO_PHONE_NUMBER')
twilio_client = Client(TWILIO_SID, TWILIO_TOKEN) if TWILIO_SID and TWILIO_TOKEN else None

creds = service_account.Credentials.from_service_account_info(
    json.loads(os.environ['GOOGLE_CREDENTIALS_JSON']),
    scopes=['https://www.googleapis.com/auth/calendar']
)
service = build('calendar', 'v3', credentials=creds)

lembretes_enviados = set()

def parse(msg):
    hoje = datetime.now()
    msg_lower = msg.lower().strip()
    
    eh_lembrete = 'lembrete' in msg_lower
    
    # Cor
    cor = '9'
    for nome, codigo in CORES.items():
        if nome in msg_lower:
            cor = codigo
            msg_lower = msg_lower.replace(nome, '')
            break
    
    msg_lower = re.sub(r'(lembrete|de\s+)', '', msg_lower).strip()
    
    # DATA - ordem importante!
    data = None
    
    # "daqui a X minutos/horas"
    if m := re.search(r'daqui\s+a\s+(\d+)\s*(minutos?|mins?|h|horas?)', msg_lower):
        quantidade = int(m.group(1))
        unidade = m.group(2)
        if unidade.startswith('min') or unidade == 'm':
            data = hoje + timedelta(minutes=quantidade)
        else:
            data = hoje + timedelta(hours=quantidade)
        msg_lower = msg_lower.replace(m.group(0), '')
        # Para "daqui a X minutos", usamos a hora atual + offset
        return msg_lower.strip().title() or "Lembrete", data, eh_lembrete, cor
    
    # "amanhã"
    elif 'amanhã' in msg_lower or 'amanha' in msg_lower:
        data = hoje + timedelta(days=1)
        msg_lower = re.sub(r'amanh[ãa]', '', msg_lower)
    
    # "hoje"
    elif 'hoje' in msg_lower:
        data = hoje
        msg_lower = msg_lower.replace('hoje', '')
    
    # dias da semana
    elif m := re.search(r'\b(segunda|terça|terca|quarta|quinta|sexta|sábado|sabado|domingo)\b', msg_lower):
        dias = {'segunda':0,'terça':1,'terca':1,'quarta':2,'quinta':3,'sexta':4,'sábado':5,'sabado':5,'domingo':6}
        ate = (dias[m.group()] - hoje.weekday()) % 7 or 7
        data = hoje + timedelta(days=ate)
        msg_lower = msg_lower.replace(m.group(), '')
    
    # data específica (dia 25, 25/03, etc) - só se não for "daqui a"
    elif m := re.search(r'\b(\d{1,2})(?:/(\d{1,2}))?(?:/(\d{2,4}))?\b', msg_lower):
        dia = int(m.group(1))
        mes = int(m.group(2)) if m.group(2) else hoje.month
        ano = int(m.group(3)) if m.group(3) else hoje.year
        
        # Ajusta ano de 2 dígitos
        if ano < 100:
            ano = 2000 + ano
        
        try:
            data = datetime(ano, mes, dia)
            # Se data já passou este ano, vai para próximo
            if data.date() < hoje.date():
                data = datetime(ano + 1, mes, dia)
        except:
            return None
        msg_lower = msg_lower[:m.start()] + msg_lower[m.end():]
    
    else:
        # Se não achou data e não é "daqui a", assume hoje
        data = hoje
    
    # HORA
    hora, minuto = 9, 0
    
    # Procura padrões de hora
    padroes = [
        (r'(?:às\s*)?(\d{1,2}):(\d{2})', lambda m: (int(m.group(1)), int(m.group(2)))),  # 15:30
        (r'(\d{1,2})h(\d{2})', lambda m: (int(m.group(1)), int(m.group(2)))),              # 15h30
        (r'(\d{1,2})\s*(?:h|horas?)\b', lambda m: (int(m.group(1)), 0)),                  # 15h
        (r'\b(?:às?\s+)(\d{1,2})\b', lambda m: (int(m.group(1)), 0)),                     # às 15
    ]
    
    for padrao, extrator in padroes:
        if m := re.search(padrao, msg_lower):
            hora, minuto = extrator(m)
            # Ajusta período
            if 'tarde' in msg_lower and hora < 12:
                hora += 12
            elif 'noite' in msg_lower and hora < 12:
                hora += 12
            msg_lower = msg_lower.replace(m.group(0), '')
            break
    
    # Monta datetime final
    inicio = data.replace(hour=hora, minute=minuto, second=0, microsecond=0)
    
    # Se já passou hoje (e não é lembrete com data específica), vai para amanhã
    if inicio < hoje and not eh_lembrete:
        inicio += timedelta(days=1)
    
    # Título
    titulo = re.sub(r'\s+', ' ', msg_lower).strip().title()
    if len(titulo) < 2:
        titulo = "Lembrete" if eh_lembrete else "Evento"
    
    return titulo, inicio, eh_lembrete, cor

def enviar_whatsapp(numero, mensagem):
    if not twilio_client:
        print(f"❌ Twilio não configurado: {mensagem}")
        return False
    try:
        numero_limpo = numero.replace('whatsapp:', '')
        if not numero_limpo.startswith('+'):
            numero_limpo = '+' + numero_limpo
        msg = twilio_client.messages.create(
            body=mensagem,
            from_=f'whatsapp:{TWILIO_NUMBER}',
            to=f'whatsapp:{numero_limpo}'
        )
        print(f"✅ WhatsApp enviado: {msg.sid}")
        return True
    except Exception as e:
        print(f"❌ Erro Twilio: {e}")
        return False

def verificar_lembretes():
    print("🔄 Thread de lembretes iniciada")
    while True:
        try:
            agora = datetime.now()
            # Busca eventos nos próximos 2 minutos
            time_min = agora.isoformat() + 'Z'
            time_max = (agora + timedelta(minutes=2)).isoformat() + 'Z'
            
            print(f"🔍 Verificando... {agora.strftime('%H:%M:%S')}")
            
            events = service.events().list(
                calendarId=CAL_ID_LEMBRETES,
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy='startTime',
                q='[LEMBRETE]'
            ).execute()
            
            print(f"   Encontrados: {len(events.get('items', []))}")
            
            for event in events.get('items', []):
                event_id = event.get('id')
                if event_id in lembretes_enviados:
                    continue
                
                titulo = event.get('summary', '').replace('[LEMBRETE] ', '')
                descricao = event.get('description', '')
                numero = descricao.replace('Numero: ', '').strip()
                
                mensagem = f"⏰ *Lembrete!*\n\n*{titulo}*\n⏰ {agora.strftime('%H:%M')}"
                
                print(f"   📤 Enviando: {titulo}")
                if enviar_whatsapp(numero, mensagem):
                    lembretes_enviados.add(event_id)
                    print(f"   ✅ OK!")
                    
        except Exception as e:
            print(f"❌ Erro: {e}")
        
        time.sleep(60)

threading.Thread(target=verificar_lembretes, daemon=True).start()

@app.route("/webhook", methods=['POST'])
def webhook():
    msg = request.values.get('Body', '').strip()
    numero = request.values.get('From', '')
    resp = MessagingResponse()
    
    if msg.lower() in ['ajuda', 'help']:
        resp.message("""🤖 *Bot de Agenda*

*Eventos:*
• reunião amanhã 15h
• médico segunda 14:30 vermelho

*Lembretes:*
• lembrete daqui a 5 minutos
• lembrete pagar conta amanhã 15h

*Cores:* vermelho, laranja, amarelo, verde, azul, roxo""")
        return str(resp)
    
    if not (p := parse(msg)):
        resp.message("❓ Não entendi. Envie 'ajuda' para exemplos.")
        return str(resp)
    
    titulo, inicio, eh_lembrete, cor = p
    
    # Debug
    print(f"📝 Parsed: {titulo} | {inicio} | Lembrete: {eh_lembrete}")
    
    if eh_lembrete:
        evento = {
            'summary': f'[LEMBRETE] {titulo}',
            'description': f'Numero: {numero}',
            'start': {'dateTime': inicio.isoformat(), 'timeZone': 'America/Sao_Paulo'},
            'end': {'dateTime': (inicio + timedelta(minutes=1)).isoformat(), 'timeZone': 'America/Sao_Paulo'},
            'colorId': '5',
            'reminders': {'useDefault': False}
        }
        
        try:
            ev = service.events().insert(calendarId=CAL_ID_LEMBRETES, body=evento).execute()
            resp.message(f"""⏰ *Lembrete agendado!*

*{titulo}*
📅 {inicio.strftime('%d/%m/%Y %H:%M')}

💬 Vou te avisar na hora!""")
        except Exception as e:
            resp.message(f"❌ Erro: {str(e)[:100]}")
    else:
        evento = {
            'summary': titulo,
            'colorId': cor,
            'start': {'dateTime': inicio.isoformat(), 'timeZone': 'America/Sao_Paulo'},
            'end': {'dateTime': (inicio + timedelta(hours=1)).isoformat(), 'timeZone': 'America/Sao_Paulo'},
            'reminders': {'useDefault': False, 'overrides': [{'method': 'popup', 'minutes': 30}]}
        }
        
        try:
            ev = service.events().insert(calendarId=CAL_ID_EVENTOS, body=evento).execute()
            emoji_cor = {'11': '🔴', '6': '🟠', '5': '🟡', '10': '🟢', '9': '🔵', '3': '🟣'}.get(cor, '🔵')
            resp.message(f"{emoji_cor} 📅 *{titulo}*\n📆 {inicio.strftime('%d/%m/%Y %H:%M')}\n\n🔗 {ev.get('htmlLink')}")
        except Exception as e:
            resp.message(f"❌ Erro: {str(e)[:100]}")
    
    return str(resp)

@app.route("/")
def health():
    return jsonify({"status": "ok", "twilio": twilio_client is not None})

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
