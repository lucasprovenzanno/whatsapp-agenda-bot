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
# CONFIGURAÇÃO
# ============================================================================

SCOPES = ['https://www.googleapis.com/auth/calendar']
MEU_CALENDARIO_ID = 'lucasprovenzano.cobeb@gmail.com'

# ============================================================================
# GOOGLE CALENDAR - SERVICE ACCOUNT
# ============================================================================

def get_calendar_service():
    """Autenticação via Service Account"""
    try:
        creds_json = os.environ.get('GOOGLE_CREDENTIALS_JSON')
        if not creds_json:
            raise Exception("Variável GOOGLE_CREDENTIALS_JSON não encontrada")
        
        creds_info = json.loads(creds_json)
        credentials = service_account.Credentials.from_service_account_info(
            creds_info, scopes=SCOPES
        )
        return build('calendar', 'v3', credentials=credentials)
    except Exception as e:
        print(f"Erro auth: {e}")
        raise

try:
    calendar_service = get_calendar_service()
    print("✅ Google Calendar conectado")
except Exception as e:
    print(f"❌ Erro: {e}")
    calendar_service = None

# ============================================================================
# CORES
# ============================================================================

CORES = {
    'vermelho': '11', 'laranja': '6', 'amarelo': '5',
    'verde': '10', 'azul': '9', 'roxo': '3'
}

PALAVRAS_CORES = {
    'vermelho': ['academia', 'médico', 'medico', 'consulta', 'remedio', 'pessoal', 'treino'],
    'laranja': ['reunião', 'reuniao', 'trabalho', 'cliente', 'call', 'meet'],
    'amarelo': ['importante', 'urgente', 'prazo', 'deadline', 'pagamento'],
    'verde': ['estudo', 'curso', 'aula', 'faculdade', 'prova'],
    'roxo': ['lazer', 'festa', 'bar', 'restaurante', 'cinema', 'viagem']
}

def detectar_cor(titulo):
    t = titulo.lower()
    for cor, palavras in PALAVRAS_CORES.items():
        if any(p in t for p in palavras):
            return CORES[cor]
    return CORES['azul']

# ============================================================================
# PARSE DE DATA/HORA
# ============================================================================

def parse_data_hora(msg):
    hoje = datetime.now()
    msg_lower = msg.lower().strip()
    
    # Remove prefixos
    msg_lower = re.sub(r'^(me\s+lembre\s+(de\s+)?|criar\s+|marcar\s+|adicionar\s+)', '', msg_lower)
    
    dias = {'segunda': 0, 'terça': 1, 'quarta': 2, 'quinta': 3, 'sexta': 4, 'sábado': 5, 'domingo': 6}
    
    data = None
    resto = msg_lower
    
    # Amanhã
    if 'amanhã' in msg_lower or 'amanha' in msg_lower:
        data = hoje + timedelta(days=1)
        resto = re.sub(r'amanh[ãa]', '', resto)
    
    # Dias da semana
    elif any(d in msg_lower for d in dias):
        for d, n in dias.items():
            if d in msg_lower:
                ate = (n - hoje.weekday()) % 7
                if ate == 0: ate = 7
                data = hoje + timedelta(days=ate)
                resto = resto.replace(d, '')
                break
    
    # Hoje
    elif 'hoje' in msg_lower:
        data = hoje
        resto = resto.replace('hoje', '')
    
    # Data específica
    elif m := re.search(r'(?:dia\s+)?(\d{1,2})(?:/(\d{1,2}))?', msg_lower):
        dia, mes = int(m.group(1)), int(m.group(2)) if m.group(2) else hoje.month
        ano = hoje.year
        try:
            d = datetime(ano, mes, dia)
            if d < hoje.replace(hour=0, minute=0, second=0): d = datetime(ano+1, mes, dia)
            data = d
        except: return None
        resto = resto[:m.start()] + resto[m.end():]
    
    else:
        return None
    
    # Hora
    hora, minuto = 9, 0
    if m := re.search(r'(\d{1,2})[:h]?(\d{2})?(?:\s*h)?(?:\s*(manhã|tarde|noite))?', resto):
        hora = int(m.group(1))
        minuto = int(m.group(2)) if m.group(2) else 0
        if m.group(3) in ['tarde', 'noite'] and hora < 12: hora += 12
    
    inicio = data.replace(hour=hora, minute=minuto, second=0)
    if inicio < hoje: inicio += timedelta(days=1)
    
    # Título
    titulo = re.sub(r'(\d{1,2})[:h]?(\d{2})?(?:\s*h)?', '', resto)
    titulo = re.sub(r'\s+', ' ', titulo).strip().title()
    if len(titulo) < 2: titulo = "Evento"
    
    return titulo, inicio, inicio + timedelta(hours=1)

# ============================================================================
# CRIAR EVENTO
# ============================================================================

def criar_evento(titulo, inicio, fim):
    if not calendar_service:
        return {'erro': 'Serviço indisponível'}
    
    evento = {
        'summary': titulo,
        'start': {'dateTime': inicio.isoformat(), 'timeZone': 'America/Sao_Paulo'},
        'end': {'dateTime': fim.isoformat(), 'timeZone': 'America/Sao_Paulo'},
        'colorId': detectar_cor(titulo),
        'reminders': {'useDefault': False, 'overrides': [{'method': 'popup', 'minutes': 30}]}
    }
    
    # Tenta seu calendário, depois primary
    for cal_id in [MEU_CALENDARIO_ID, 'primary']:
        try:
            ev = calendar_service.events().insert(calendarId=cal_id, body=evento).execute()
            return {'ok': True, 'link': ev.get('htmlLink'), 'titulo': titulo, 'inicio': inicio.strftime('%d/%m %H:%M')}
        except HttpError as e:
            print(f"Erro {cal_id}: {e.resp.status}")
            continue
    
    return {'erro': 'Acesso negado. Verifique compartilhamento do calendário.'}

# ============================================================================
# ROTAS
# ============================================================================

@app.route("/webhook", methods=['POST'])
def webhook():
    msg = request.values.get('Body', '').strip()
    print(f"📩 {msg}")
    
    resp = MessagingResponse()
    
    # Comandos
    if msg.lower() in ['ajuda', 'help', 'menu']:
        resp.message("""🤖 *Comandos:*
• reunião amanhã 15h
• academia segunda 6h
• médico dia 25/03 14h
• status - verificar conexão""")
        return str(resp)
    
    if msg.lower() == 'status':
        status = "✅ Online" if calendar_service else "❌ Erro"
        resp.message(f"{status}\n📅 Agenda: {MEU_CALENDARIO_ID}")
        return str(resp)
    
    # Criar evento
    if parsed := parse_data_hora(msg):
        titulo, inicio, fim = parsed
        resultado = criar_evento(titulo, inicio, fim)
        
        if resultado.get('ok'):
            emoji = {'11': '🔴', '6': '🟠', '5': '🟡', '10': '🟢', '9': '🔵', '3': '🟣'}.get(detectar_cor(titulo), '🔵')
            resp.message(f"""✅ *Evento criado!*

{emoji} {resultado['titulo']}
📅 {resultado['inicio']}

🔗 {resultado['link']}""")
        else:
            resp.message(f"""❌ {resultado['erro']}

Verifique se compartilhou o calendório com:
whatsapp-agenda-bot-537@agenda-489719.iam.gserviceaccount.com""")
    else:
        resp.message("❓ Não entendi. Tente: reunião amanhã 15h")
    
    return str(resp)

@app.route("/", methods=['GET'])
def health():
    return jsonify({"status": "ok", "calendar": calendar_service is not None})

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
