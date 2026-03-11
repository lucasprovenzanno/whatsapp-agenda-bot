import os, json, re, threading, time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from flask import Flask, request, jsonify
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
from google.oauth2 import service_account
from googleapiclient.discovery import build

app = Flask(__name__)

FUSO = ZoneInfo('America/Sao_Paulo')

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

# ==========================================
# SISTEMA DE CANCELAMENTO (EXISTENTE)
# ==========================================

user_cancel_sessions = {}

def formatar_data_br(start_dict):
    """Converte data do Google Calendar para formato amigável em PT-BR"""
    
    # Evento de dia inteiro (sem hora específica)
    if 'date' in start_dict:
        data = datetime.strptime(start_dict['date'], '%Y-%m-%d')
        dias_semana = ['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo']
        return f"{dias_semana[data.weekday()]} {data.day:02d}/{data.month:02d} (dia todo)"
    
    # Evento com horário - extrai data/hora do ISO format
    data_iso = start_dict['dateTime']
    
    if '+' in data_iso:
        data_limpa = data_iso[:data_iso.rfind('+')]
    else:
        partes = data_iso.rsplit('-', 1)
        data_limpa = partes[0]
    
    try:
        data = datetime.fromisoformat(data_limpa)
    except:
        data = datetime.strptime(data_limpa[:19], '%Y-%m-%dT%H:%M:%S')
    
    data = data.replace(tzinfo=FUSO)
    agora = datetime.now(FUSO)
    amanha = agora + timedelta(days=1)
    
    dias_semana = ['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo']
    
    if data.date() == agora.date():
        prefixo = 'Hoje'
    elif data.date() == amanha.date():
        prefixo = 'Amanhã'
    else:
        prefixo = f"{dias_semana[data.weekday()]} {data.day:02d}/{data.month:02d}"
    
    hora = data.strftime('%H:%M')
    return f"{prefixo} às {hora}"


def listar_eventos_cancelar(phone_number):
    """Busca eventos dos próximos 7 dias para cancelamento"""
    
    try:
        agora = datetime.now(FUSO)
        time_min = agora.isoformat()
        daqui_7_dias = (agora + timedelta(days=7)).isoformat()
        
        events_result = service.events().list(
            calendarId=CAL_ID_EVENTOS,
            timeMin=time_min,
            timeMax=daqui_7_dias,
            singleEvents=True,
            orderBy='startTime',
            maxResults=20
        ).execute()
        
        eventos = events_result.get('items', [])
        
        if not eventos:
            return '✅ Não há eventos nos próximos 7 dias para cancelar.'
        
        user_cancel_sessions[phone_number] = {
            'eventos': eventos,
            'timestamp': datetime.now()
        }
        
        mensagem = '🗑️ *Eventos dos próximos 7 dias:*\n\n'
        
        for i, evento in enumerate(eventos, 1):
            titulo = evento.get('summary', 'Sem título')
            data_formatada = formatar_data_br(evento['start'])
            mensagem += f"{i}. *{titulo}*\n   📅 {data_formatada}\n\n"
        
        mensagem += 'Para cancelar, responda:\n*cancelar número*\n_(ex: cancelar 2)_'
        
        return mensagem
        
    except Exception as e:
        print(f"Erro ao listar: {e}")
        return '❌ Erro ao buscar eventos. Tente novamente.'


def confirmar_cancelamento(phone_number, numero_escolhido):
    """Cancela o evento escolhido pelo número"""
    
    sessao = user_cancel_sessions.get(phone_number)
    
    if not sessao:
        return '⚠️ Lista expirada. Envie *cancelar* para ver os eventos novamente.'
    
    if datetime.now() - sessao['timestamp'] > timedelta(minutes=5):
        user_cancel_sessions.pop(phone_number, None)
        return '⏰ Tempo expirado. Envie *cancelar* para ver os eventos novamente.'
    
    eventos = sessao['eventos']
    indice = numero_escolhido - 1
    
    if indice < 0 or indice >= len(eventos):
        return f'❌ Número inválido. Escolha entre 1 e {len(eventos)}.'
    
    evento = eventos[indice]
    
    try:
        service.events().delete(
            calendarId=CAL_ID_EVENTOS,
            eventId=evento['id']
        ).execute()
        
        titulo_removido = evento.get('summary', 'Evento')
        data_removida = formatar_data_br(evento['start'])
        eventos.pop(indice)
        
        return f'✅ *Cancelado com sucesso!*\n\n🗑️ {titulo_removido}\n📅 {data_removida}'
        
    except Exception as e:
        print(f"Erro ao cancelar: {e}")
        return '❌ Erro ao cancelar. O evento pode já ter sido removido.'

# ==========================================
# RESUMO DA SEMANA (NOVO)
# ==========================================

def resumo_semana():
    """Retorna resumo dos próximos 7 dias em formato de agenda visual"""
    
    try:
        agora = datetime.now(FUSO)
        time_min = agora.isoformat()
        daqui_7_dias = (agora + timedelta(days=7)).isoformat()
        
        events_result = service.events().list(
            calendarId=CAL_ID_EVENTOS,
            timeMin=time_min,
            timeMax=daqui_7_dias,
            singleEvents=True,
            orderBy='startTime',
            maxResults=50
        ).execute()
        
        eventos = events_result.get('items', [])
        
        if not eventos:
            return '📅 *Sua agenda dos próximos 7 dias:*\n\n✅ Nenhum evento agendado!'
        
        # Agrupa por dia
        eventos_por_dia = {}
        
        for evento in eventos:
            start = evento['start']
            
            # Pega a data (sem hora) para agrupar
            if 'date' in start:
                data_key = start['date']
            else:
                data_iso = start['dateTime'][:10]  # Pega só YYYY-MM-DD
                data_key = data_iso
            
            if data_key not in eventos_por_dia:
                eventos_por_dia[data_key] = []
            
            eventos_por_dia[data_key].append(evento)
        
        # Ordena as datas
        datas_ordenadas = sorted(eventos_por_dia.keys())
        
        # Monta mensagem agrupada por dia
        mensagem = '📅 *Sua agenda - Próximos 7 dias*\n'
        mensagem += f'_{agora.strftime("%d/%m/%Y")} a {(agora + timedelta(days=7)).strftime("%d/%m/%Y")}_\n\n'
        
        for data_str in datas_ordenadas:
            eventos_do_dia = eventos_por_dia[data_str]
            
            # Formata cabeçalho do dia
            data_obj = datetime.strptime(data_str, '%Y-%m-%d')
            dias_semana = ['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo']
            
            hoje = agora.date()
            data_evento = data_obj.date()
            
            if data_evento == hoje:
                dia_titulo = "📍 *HOJE*"
            elif data_evento == hoje + timedelta(days=1):
                dia_titulo = "🔜 *AMANHÃ*"
            else:
                nome_dia = dias_semana[data_obj.weekday()]
                dia_titulo = f"📆 *{nome_dia}* ({data_obj.day:02d}/{data_obj.month:02d})"
            
            mensagem += f"{dia_titulo}\n"
            
            # Lista eventos do dia
            for evento in eventos_do_dia:
                titulo = evento.get('summary', 'Sem título')
                
                # Pega cor do evento (se tiver)
                cor_id = evento.get('colorId', '9')
                emoji_cor = {'11': '🔴', '6': '🟠', '5': '🟡', '10': '🟢', '9': '🔵', '3': '🟣'}.get(cor_id, '⚪')
                
                # Formata hora
                if 'date' in evento['start']:
                    hora_str = "dia todo"
                else:
                    hora = datetime.fromisoformat(evento['start']['dateTime'][:19])
                    hora_str = hora.strftime('%H:%M')
                
                mensagem += f"  {emoji_cor} {hora_str} — {titulo}\n"
            
            mensagem += "\n"
        
        total = len(eventos)
        mensagem += f"📊 *Total: {total} evento{'s' if total > 1 else ''}*"
        
        return mensagem
        
    except Exception as e:
        print(f"Erro no resumo: {e}")
        return '❌ Erro ao buscar agenda. Tente novamente.'

# ==========================================
# FIM DAS FUNÇÕES AUXILIARES
# ==========================================

def agora_sp():
    return datetime.now(FUSO)

def parse(msg):
    agora = agora_sp()
    msg_lower = msg.lower().strip()
    
    eh_lembrete = 'lembrete' in msg_lower
    
    cor = '9'
    for nome, codigo in CORES.items():
        if nome in msg_lower:
            cor = codigo
            msg_lower = msg_lower.replace(nome, '')
            break
    
    msg_lower = re.sub(r'(lembrete|de\s+)', '', msg_lower).strip()
    
    data_hora = None
    
    if m := re.search(r'daqui\s+a\s+(\d+)\s*(minutos?|mins?|m\b|h|horas?)', msg_lower):
        quantidade = int(m.group(1))
        unidade = m.group(2)
        if unidade.startswith('min') or unidade == 'm' or len(unidade) <= 2:
            data_hora = agora + timedelta(minutes=quantidade)
        else:
            data_hora = agora + timedelta(hours=quantidade)
        msg_lower = msg_lower.replace(m.group(0), '')
        titulo = msg_lower.strip().title() or "Lembrete"
        return titulo, data_hora, eh_lembrete, cor
    
    elif 'amanhã' in msg_lower or 'amanha' in msg_lower:
        data_base = agora + timedelta(days=1)
        msg_lower = re.sub(r'amanh[ãa]', '', msg_lower)
    
    elif 'hoje' in msg_lower:
        data_base = agora
        msg_lower = msg_lower.replace('hoje', '')
    
    elif m := re.search(r'\b(segunda|terça|terca|quarta|quinta|sexta|sábado|sabado|domingo)\b', msg_lower):
        dias = {'segunda':0,'terça':1,'terca':1,'quarta':2,'quinta':3,'sexta':4,'sábado':5,'sabado':5,'domingo':6}
        ate = (dias[m.group()] - agora.weekday()) % 7 or 7
        data_base = agora + timedelta(days=ate)
        msg_lower = msg_lower.replace(m.group(), '')
    
    elif m := re.search(r'\b(\d{1,2})(?:/(\d{1,2}))?(?:/(\d{2,4}))?\b', msg_lower):
        dia = int(m.group(1))
        mes = int(m.group(2)) if m.group(2) else agora.month
        ano = int(m.group(3)) if m.group(3) else agora.year
        
        if ano < 100:
            ano = 2000 + ano
        
        try:
            data_base = datetime(ano, mes, dia, tzinfo=FUSO)
            if data_base.date() < agora.date():
                data_base = datetime(ano + 1, mes, dia, tzinfo=FUSO)
        except:
            return None
        msg_lower = msg_lower[:m.start()] + msg_lower[m.end():]
    
    else:
        data_base = agora
    
    hora, minuto = 9, 0
    
    padroes = [
        (r'(?:às\s*)?(\d{1,2}):(\d{2})', lambda m: (int(m.group(1)), int(m.group(2)))),
        (r'(\d{1,2})h(\d{2})', lambda m: (int(m.group(1)), int(m.group(2)))),
        (r'(\d{1,2})\s*(?:h|horas?)\b', lambda m: (int(m.group(1)), 0)),
        (r'\b(?:às?\s+)(\d{1,2})\b', lambda m: (int(m.group(1)), 0)),
    ]
    
    for padrao, extrator in padroes:
        if m := re.search(padrao, msg_lower):
            hora, minuto = extrator(m)
            msg_teste = msg_lower
            if 'tarde' in msg_teste and hora < 12:
                hora += 12
            elif 'noite' in msg_teste and hora < 12:
                hora += 12
            msg_lower = msg_lower.replace(m.group(0), '')
            break
    
    data_hora = data_base.replace(hour=hora, minute=minuto, second=0, microsecond=0, tzinfo=FUSO)
    
    if data_hora < agora and not eh_lembrete:
        data_hora += timedelta(days=1)
    
    titulo = re.sub(r'\s+', ' ', msg_lower).strip().title()
    if len(titulo) < 2:
        titulo = "Lembrete" if eh_lembrete else "Evento"
    
    return titulo, data_hora, eh_lembrete, cor

def enviar_whatsapp(numero, mensagem):
    if not twilio_client:
        print(f"❌ Twilio não configurado")
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
        print(f"✅ WhatsApp: {msg.sid}")
        return True
    except Exception as e:
        print(f"❌ Erro Twilio: {e}")
        return False

def verificar_lembretes():
    print("🔄 Thread lembretes iniciada")
    while True:
        try:
            agora = agora_sp()
            time_min = agora.isoformat()
            time_max = (agora + timedelta(minutes=2)).isoformat()
            
            print(f"🔍 {agora.strftime('%d/%m %H:%M:%S')}")
            
            events = service.events().list(
                calendarId=CAL_ID_LEMBRETES,
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy='startTime',
                q='[LEMBRETE]'
            ).execute()
            
            print(f"   {len(events.get('items', []))} lembretes")
            
            for event in events.get('items', []):
                event_id = event.get('id')
                if event_id in lembretes_enviados:
                    continue
                
                titulo = event.get('summary', '').replace('[LEMBRETE] ', '')
                descricao = event.get('description', '')
                numero = descricao.replace('Numero: ', '').strip()
                
                mensagem = f"⏰ *Lembrete!*\n\n*{titulo}*\n🕐 {agora.strftime('%H:%M')}"
                
                print(f"   📤 {titulo}")
                if enviar_whatsapp(numero, mensagem):
                    lembretes_enviados.add(event_id)
                    
        except Exception as e:
            print(f"❌ Erro: {e}")
        
        time.sleep(60)

threading.Thread(target=verificar_lembretes, daemon=True).start()

@app.route("/webhook", methods=['POST'])
def webhook():
    msg = request.values.get('Body', '').strip()
    numero = request.values.get('From', '')
    resp = MessagingResponse()
    
    msg_lower = msg.lower()
    
    # ==========================================
    # COMANDOS PRIORITÁRIOS
    # ==========================================
    
    # NOVO: Resumo da semana
    if msg_lower in ['agenda semana', 'agenda da semana', 'minha semana', 'próximos 7 dias']:
        resposta = resumo_semana()
        resp.message(resposta)
        return str(resp)
    
    # Cancelar existente
    match_cancelar_numero = re.match(r'^cancelar\s+(\d+)$', msg_lower)
    
    if msg_lower == 'cancelar':
        resposta = listar_eventos_cancelar(numero)
        resp.message(resposta)
        return str(resp)
    
    elif match_cancelar_numero:
        numero_escolhido = int(match_cancelar_numero.group(1))
        resposta = confirmar_cancelamento(numero, numero_escolhido)
        resp.message(resposta)
        return str(resp)
    
    # ==========================================
    # COMANDOS EXISTENTES
    # ==========================================
    
    if msg_lower in ['ajuda', 'help']:
        resp.message("""🤖 *Bot de Agenda*

*Eventos:*
• reunião amanhã 15h
• médico segunda 14:30 vermelho

*Lembretes:*
• lembrete daqui a 5 minutos
• lembrete pagar conta amanhã 15h

*Agenda:*
• agenda semana (próximos 7 dias)

*Cancelar:*
• cancelar (lista eventos)
• cancelar 2 (cancela o #2)

*Cores:* vermelho, laranja, amarelo, verde, azul, roxo""")
        return str(resp)
    
    if not (p := parse(msg)):
        resp.message("❓ Não entendi. Envie 'ajuda' para exemplos.")
        return str(resp)
    
    titulo, inicio, eh_lembrete, cor = p
    
    print(f"📝 {titulo} | {inicio.strftime('%d/%m %H:%M')} | Lembrete:{eh_lembrete}")
    
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

💬 Te aviso na hora!""")
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
    agora = agora_sp()
    return jsonify({
        "status": "ok",
        "hora": agora.strftime('%d/%m %H:%M:%S'),
        "twilio": twilio_client is not None
    })

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
