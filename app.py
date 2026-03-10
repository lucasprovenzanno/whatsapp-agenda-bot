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

SCOPES = ['https://www.googleapis.com/auth/calendar']
MEU_CALENDARIO_ID = 'lucasprovenzano.cobeb@gmail.com'

# ============================================================================
# AUTH
# ============================================================================

def get_calendar_service():
    creds_json = os.environ.get('GOOGLE_CREDENTIALS_JSON')
    if not creds_json:
        raise Exception("GOOGLE_CREDENTIALS_JSON não encontrada")
    creds_info = json.loads(creds_json)
    credentials = service_account.Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    return build('calendar', 'v3', credentials=credentials)

try:
    calendar_service = get_calendar_service()
    print("✅ Auth OK")
except Exception as e:
    print(f"❌ Auth erro: {e}")
    calendar_service = None

# ============================================================================
# CORES & PARSE (simplificado)
# ============================================================================

def detectar_cor(titulo):
    t = titulo.lower()
    if any(p in t for p in ['academia', 'médico', 'treino']): return '11'
    if any(p in t for p in ['reunião', 'trabalho', 'call']): return '6'
    if any(p in t for p in ['importante', 'urgente', 'prazo']): return '5'
    if any(p in t for p in ['estudo', 'curso', 'aula']): return '10'
    if any(p in t for p in ['lazer', 'festa', 'bar']): return '3'
    return '9'

def parse_data_hora(msg):
    hoje = datetime.now()
    msg = msg.lower()
    
    # Data
    if 'amanhã' in msg or 'amanha' in msg:
        data = hoje + timedelta(days=1)
    elif 'hoje' in msg:
        data = hoje
    elif m := re.search(r'segunda|terça|quarta|quinta|sexta|sábado|domingo', msg):
        dias = {'segunda':0,'terça':1,'quarta':2,'quinta':3,'sexta':4,'sábado':5,'domingo':6}
        ate = (dias[m.group()] - hoje.weekday()) % 7
        if ate == 0: ate = 7
        data = hoje + timedelta(days=ate)
    elif m := re.search(r'(\d{1,2})(?:/(\d{1,2}))?', msg):
        dia, mes = int(m.group(1)), int(m.group(2)) if m.group(2) else hoje.month
        try:
            data = datetime(hoje.year, mes, dia)
            if data < hoje: data = datetime(hoje.year+1, mes, dia)
        except: return None
    else:
        return None
    
    # Hora
    hora, minuto = 9, 0
    if m := re.search(r'(\d{1,2})[:h]?(\d{2})?', msg):
        hora = int(m.group(1))
        if m.group(2): minuto = int(m.group(2))
        if 'tarde' in msg and hora < 12: hora += 12
        if 'noite' in msg and hora < 12: hora += 12
    
    inicio = data.replace(hour=hora, minute=minuto, second=0)
    if inicio < hoje: inicio += timedelta(days=1)
    
    # Título
    titulo = re.sub(r'(\d{1,2})[:h]?(\d{2})?|amanh[ãa]|hoje|dia|da\s*manhã|da\s*tarde|da\s*noite', '', msg)
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
    
    # Tenta seu calendário primeiro
    for cal_id in [MEU_CALENDARIO_ID, 'primary']:
        try:
            ev = calendar_service.events().insert(calendarId=cal_id, body=evento).execute()
            return {'ok': True, 'link': ev.get('htmlLink'), 'cal': cal_id}
        except HttpError as e:
            print(f"Erro {cal_id}: {e.resp.status} - {e._get_reason()}")
            continue
    
    return {'erro': 'Acesso negado aos calendários'}

# ============================================================================
# ROTAS
# ============================================================================

@app.route("/webhook", methods=['POST'])
def webhook():
    msg = request.values.get('Body', '').strip()
    print(f"📩 {msg}")
    resp = MessagingResponse()
    
    if msg.lower() in ['ajuda', 'help']:
        resp.message("🤖 Comandos: reunião amanhã 15h, status, ajuda")
        return str(resp)
    
    if msg.lower() == 'status':
        if not calendar_service:
            resp.message("❌ Serviço indisponível")
            return str(resp)
        try:
            cals = calendar_service.calendarList().list().execute()
            resp.message(f"✅ Online\n📅 Calendários: {len(cals.get('items', []))}")
        except Exception as e:
            resp.message(f"⚠️ Erro: {str(e)[:100]}")
        return str(resp)
    
    if parsed := parse_data_hora(msg):
        titulo, inicio, fim = parsed
        r = criar_evento(titulo, inicio, fim)
        if r.get('ok'):
            resp.message(f"✅ {titulo}\n🔗 {r['link']}")
        else:
            resp.message(f"❌ {r['erro']}\n\nVerifique se compartilhou:\nwhatsapp-agenda-bot-537@agenda-489719.iam.gserviceaccount.com")
    else:
        resp.message("❓ Não entendi. Ex: reunião amanhã 15h")
    
    return str(resp)

@app.route("/", methods=['GET'])
def health():
    return jsonify({"status": "ok"})

# ============================================================================
# DIAGNÓSTICO COMPLETO - Acesse /diagnostico
# ============================================================================

@app.route("/diagnostico", methods=['GET'])
def diagnostico():
    """Endpoint para verificar tudo"""
    resultado = {
        "timestamp": datetime.now().isoformat(),
        "auth": False,
        "calendarios_listados": [],
        "erros": [],
        "testes": {}
    }
    
    if not calendar_service:
        resultado["erros"].append("calendar_service não inicializado")
        return jsonify(resultado), 500
    
    # Teste 1: Listar calendários
    try:
        cals = calendar_service.calendarList().list().execute()
        resultado["auth"] = True
        for cal in cals.get('items', []):
            resultado["calendarios_listados"].append({
                "id": cal.get('id'),
                "nome": cal.get('summary'),
                "acesso": cal.get('accessRole')
            })
    except Exception as e:
        resultado["erros"].append(f"Erro ao listar: {str(e)}")
    
    # Teste 2: Acessar seu calendário específico
    try:
        cal = calendar_service.calendars().get(calendarId=MEU_CALENDARIO_ID).execute()
        resultado["testes"]["meu_calendario"] = {
            "status": "OK",
            "nome": cal.get('summary'),
            "timezone": cal.get('timeZone')
        }
    except HttpError as e:
        resultado["testes"]["meu_calendario"] = {
            "status": "ERRO",
            "codigo": e.resp.status,
            "mensagem": e._get_reason()
        }
    except Exception as e:
        resultado["testes"]["meu_calendario"] = {
            "status": "ERRO",
            "mensagem": str(e)
        }
    
    # Teste 3: Tentar criar evento (e apagar)
    try:
        evento_teste = {
            'summary': 'TESTE_AUTOMATICO',
            'start': {'dateTime': (datetime.now() + timedelta(days=1)).replace(hour=12, minute=0).isoformat(), 'timeZone': 'America/Sao_Paulo'},
            'end': {'dateTime': (datetime.now() + timedelta(days=1)).replace(hour=13, minute=0).isoformat(), 'timeZone': 'America/Sao_Paulo'},
        }
        ev = calendar_service.events().insert(calendarId=MEU_CALENDARIO_ID, body=evento_teste).execute()
        resultado["testes"]["criar_evento"] = {"status": "OK", "id": ev.get('id')}
        # Apaga
        calendar_service.events().delete(calendarId=MEU_CALENDARIO_ID, eventId=ev['id']).execute()
        resultado["testes"]["apagar_evento"] = {"status": "OK"}
    except HttpError as e:
        resultado["testes"]["criar_evento"] = {
            "status": "ERRO",
            "codigo": e.resp.status,
            "mensagem": e._get_reason()
        }
    except Exception as e:
        resultado["testes"]["criar_evento"] = {"status": "ERRO", "mensagem": str(e)}
    
    return jsonify(resultado)

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
