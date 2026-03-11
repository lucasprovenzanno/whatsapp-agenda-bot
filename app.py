import os, json, re, threading, time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from flask import Flask, request, jsonify
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
from google.oauth2 import service_account
from googleapiclient.discovery import build

# ==========================================
# [NOVO] 1. LOGS ESTRUTURADOS
# ==========================================
import logging
import sys
from pythonjsonlogger import jsonlogger

class CustomJsonFormatter(jsonlogger.JsonFormatter):
    def add_fields(self, log_record, record, message_dict):
        super(CustomJsonFormatter, self).add_fields(log_record, record, message_dict)
        log_record['timestamp'] = datetime.now().isoformat()
        log_record['level'] = record.levelname
        log_record['service'] = 'whatsapp-calendar-bot'

def setup_logging():
    logHandler = logging.StreamHandler(sys.stdout)
    formatter = CustomJsonFormatter('%(timestamp)s %(level)s %(service)s %(message)s')
    logHandler.setFormatter(formatter)
    
    logger = logging.getLogger()
    logger.addHandler(logHandler)
    logger.setLevel(logging.INFO)
    return logger

logger = setup_logging()

# ==========================================
# [NOVO] 2. REDIS E RATE LIMITING
# ==========================================
import redis
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

app = Flask(__name__)

# Redis client (usa variável de ambiente REDIS_URL)
redis_url = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
try:
    redis_client = redis.from_url(redis_url, decode_responses=True, socket_connect_timeout=5)
    redis_client.ping()  # Testa conexão
    logger.info("Redis conectado", extra={"url": redis_url.split('@')[1] if '@' in redis_url else 'localhost'})
except Exception as e:
    logger.error("Falha ao conectar Redis", extra={"error": str(e)})
    # Fallback para memória local (apenas para desenvolvimento)
    redis_client = None
    logger.warning("Usando fallback em memória - sessões não persistirão restart")

# Rate Limiter
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    storage_uri=redis_url if redis_client else "memory://",
    default_limits=["100 per day", "30 per hour"],
    strategy="fixed-window"
)

# ==========================================
# [NOVO] 3. GERENCIADOR DE SESSÕES COM REDIS
# ==========================================

class SessionManager:
    """Gerencia sessões de edição/cancelamento com Redis + fallback memória"""
    
    def __init__(self, redis_client, timeout=300):
        self.redis = redis_client
        self.timeout = timeout  # 5 minutos
        self.fallback = {}  # Fallback local se Redis falhar
    
    def _key(self, user_id, session_type):
        return f"session:{session_type}:{user_id}"
    
    def set(self, user_id, session_type, data):
        key = self._key(user_id, session_type)
        data['_timestamp'] = datetime.now().isoformat()
        
        if self.redis:
            try:
                self.redis.setex(key, self.timeout, json.dumps(data))
                logger.info("Sessão criada", extra={
                    "user": user_id, 
                    "type": session_type,
                    "ttl": self.timeout
                })
                return
            except Exception as e:
                logger.error("Redis falhou, usando fallback", extra={"error": str(e)})
        
        # Fallback para memória
        self.fallback[key] = data
        logger.info("Sessão criada (fallback)", extra={"user": user_id, "type": session_type})
    
    def get(self, user_id, session_type):
        key = self._key(user_id, session_type)
        
        if self.redis:
            try:
                data = self.redis.get(key)
                if data:
                    return json.loads(data)
            except Exception as e:
                logger.error("Redis falhou no get", extra={"error": str(e)})
        
        # Fallback
        return self.fallback.get(key)
    
    def delete(self, user_id, session_type):
        key = self._key(user_id, session_type)
        
        if self.redis:
            try:
                self.redis.delete(key)
            except Exception as e:
                logger.error("Redis falhou no delete", extra={"error": str(e)})
        
        self.fallback.pop(key, None)
        logger.info("Sessão removida", extra={"user": user_id, "type": session_type})
    
    def exists(self, user_id, session_type):
        return self.get(user_id, session_type) is not None

# Instanciar gerenciador
session_manager = SessionManager(redis_client, timeout=300)

FUSO = ZoneInfo('America/Sao_Paulo')

CAL_ID_EVENTOS = 'lucasprovenzano.cobeb@gmail.com'
CAL_ID_LEMBRETES = 'lucas.provenzanno@gmail.com'

CORES = {'vermelho': '11', 'laranja': '6', 'amarelo': '5', 'verde': '10', 'azul': '9', 'roxo': '3'}
CORES_INVERTIDO = {'11': 'vermelho', '6': 'laranja', '5': 'amarelo', '10': 'verde', '9': 'azul', '3': 'roxo'}

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
# FUNÇÕES AUXILIARES COMPARTILHADAS
# ==========================================

def formatar_data_br(start_dict):
    """Converte data do Google Calendar para formato amigável em PT-BR"""
    
    if 'date' in start_dict:
        data = datetime.strptime(start_dict['date'], '%Y-%m-%d')
        dias_semana = ['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo']
        return f"{dias_semana[data.weekday()]} {data.day:02d}/{data.month:02d} (dia todo)"
    
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


def emoji_por_cor(cor_id):
    """Retorna emoji baseado na cor do evento"""
    return {'11': '🔴', '6': '🟠', '5': '🟡', '10': '🟢', '9': '🔵', '3': '🟣'}.get(cor_id, '⚪')


def extrair_hora_de_string(texto):
    """Extrai horário de strings como '15h', '15:30', '15h30'"""
    padroes = [
        (r'(\d{1,2}):(\d{2})', lambda m: (int(m.group(1)), int(m.group(2)))),
        (r'(\d{1,2})h(\d{2})', lambda m: (int(m.group(1)), int(m.group(2)))),
        (r'(\d{1,2})\s*h\b', lambda m: (int(m.group(1)), 0)),
    ]
    
    for padrao, extrator in padroes:
        if m := re.search(padrao, texto.lower()):
            hora, minuto = extrator(m)
            if 'tarde' in texto.lower() and hora < 12:
                hora += 12
            elif 'noite' in texto.lower() and hora < 12:
                hora += 12
            return hora, minuto
    
    return None, None

# ==========================================
# 1. RESUMO DO DIA ESPECÍFICO
# ==========================================

def resumo_dia_especifico(dias_futuro=0, data_especifica=None, nome_dia=None):
    """Retorna eventos de um dia específico com formatação visual"""
    
    try:
        agora = datetime.now(FUSO)
        
        if data_especifica:
            data_base = data_especifica
        else:
            data_base = agora + timedelta(days=dias_futuro)
        
        inicio_dia = data_base.replace(hour=0, minute=0, second=0, microsecond=0)
        fim_dia = data_base.replace(hour=23, minute=59, second=59, microsecond=999999)
        
        events_result = service.events().list(
            calendarId=CAL_ID_EVENTOS,
            timeMin=inicio_dia.isoformat(),
            timeMax=fim_dia.isoformat(),
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        eventos = events_result.get('items', [])
        
        dias_semana = ['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo']
        nome_dia_semana = dias_semana[data_base.weekday()]
        data_formatada = f"{data_base.day:02d}/{data_base.month:02d}"
        
        if dias_futuro == 0:
            titulo_dia = "📍 *HOJE*"
        elif dias_futuro == 1:
            titulo_dia = "🔜 *AMANHÃ*"
        elif nome_dia:
            titulo_dia = f"📆 *{nome_dia.upper()}* ({data_formatada})"
        else:
            titulo_dia = f"📆 *{nome_dia_semana}* ({data_formatada})"
        
        if not eventos:
            return f'{titulo_dia}\n\n✅ Nenhum evento agendado para este dia!'
        
        mensagem = f'{titulo_dia}\n\n'
        
        for evento in eventos:
            titulo = evento.get('summary', 'Sem título')
            cor_id = evento.get('colorId', '9')
            emoji = emoji_por_cor(cor_id)
            
            if 'date' in evento['start']:
                hora_str = "dia todo"
            else:
                hora = datetime.fromisoformat(evento['start']['dateTime'][:19])
                hora_str = hora.strftime('%H:%M')
            
            mensagem += f"{emoji} {hora_str} — *{titulo}*\n"
        
        total = len(eventos)
        mensagem += f"\n📊 {total} evento{'s' if total > 1 else ''}"
        
        return mensagem
        
    except Exception as e:
        logger.error("Erro no resumo do dia", extra={"error": str(e), "dias_futuro": dias_futuro})
        return '❌ Erro ao buscar agenda. Tente novamente.'


def interpretar_comando_agenda(msg_lower):
    """Interpreta comandos de agenda e retorna parâmetros para resumo_dia_especifico"""
    
    padroes = [
        (r'^(?:agenda|o que tenho)\s+hoje$', {'dias_futuro': 0}),
        
        (r'^(?:agenda|o que tenho)\s+amanh[ãa]$', {'dias_futuro': 1}),
        
        (r'^(?:agenda|o que tenho)\s+(segunda|ter[çc]a|quarta|quinta|sexta|s[áa]bado|domingo)$', 
         lambda m: {'nome_dia': m.group(1), 'dias_futuro': calcular_dias_ate_dia(m.group(1))}),
        
        (r'^(?:agenda|o que tenho)\s+(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?$',
         lambda m: {'data_especifica': parse_data_manual(int(m.group(1)), int(m.group(2)), m.group(3))}),
    ]
    
    for padrao, params in padroes:
        if m := re.match(padrao, msg_lower):
            if callable(params):
                return params(m)
            return params
    
    return None


def calcular_dias_ate_dia(nome_dia):
    """Calcula quantos dias faltam até o próximo dia da semana"""
    dias = {
        'segunda': 0, 'terça': 1, 'terca': 1, 'quarta': 2, 
        'quinta': 3, 'sexta': 4, 'sábado': 5, 'sabado': 5, 'domingo': 6
    }
    agora = datetime.now(FUSO)
    dia_alvo = dias.get(nome_dia.lower(), 0)
    dias_ate = (dia_alvo - agora.weekday()) % 7
    if dias_ate == 0:
        dias_ate = 7
    return dias_ate


def parse_data_manual(dia, mes, ano_str=None):
    """Converte dia/mes/ano para datetime"""
    agora = datetime.now(FUSO)
    ano = int(ano_str) if ano_str else agora.year
    if ano < 100:
        ano = 2000 + ano
    
    try:
        data = datetime(ano, mes, dia, tzinfo=FUSO)
        if data.date() < agora.date():
            data = datetime(ano + 1, mes, dia, tzinfo=FUSO)
        return data
    except:
        return None

# ==========================================
# 2. EDIÇÃO DE EVENTOS (ATUALIZADO PARA REDIS)
# ==========================================

def listar_eventos_para_editar(phone_number):
    """Lista eventos dos próximos 7 dias para edição"""
    
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
            return '✅ Não há eventos nos próximos 7 dias para editar.'
        
        # [NOVO] Usa SessionManager em vez de dicionário global
        session_manager.set(phone_number, 'edicao', {
            'eventos': eventos,
            'etapa': 'escolher_evento',
            'evento_selecionado': None,
            'campo_para_editar': None
        })
        
        mensagem = '✏️ *Qual evento deseja editar?*\n\n'
        
        for i, evento in enumerate(eventos, 1):
            titulo = evento.get('summary', 'Sem título')
            data_formatada = formatar_data_br(evento['start'])
            mensagem += f"{i}. *{titulo}*\n   📅 {data_formatada}\n\n"
        
        mensagem += 'Responda: *editar número*\n_(ex: editar 2)_'
        
        return mensagem
        
    except Exception as e:
        logger.error("Erro ao listar para editar", extra={"user": phone_number, "error": str(e)})
        return '❌ Erro ao buscar eventos. Tente novamente.'


def iniciar_edicao_evento(phone_number, numero_escolhido):
    """Inicia o processo de edição após usuário escolher número"""
    
    # [NOVO] Busca do SessionManager
    sessao = session_manager.get(phone_number, 'edicao')
    
    if not sessao:
        return '⚠️ Sessão expirada. Envie *editar* para começar novamente.'
    
    eventos = sessao['eventos']
    indice = numero_escolhido - 1
    
    if indice < 0 or indice >= len(eventos):
        return f'❌ Número inválido. Escolha entre 1 e {len(eventos)}.'
    
    evento = eventos[indice]
    
    # [NOVO] Atualiza sessão
    sessao['evento_selecionado'] = evento
    sessao['etapa'] = 'escolher_campo'
    session_manager.set(phone_number, 'edicao', sessao)
    
    titulo = evento.get('summary', 'Sem título')
    hora_atual = formatar_data_br(evento['start'])
    cor_atual = CORES_INVERTIDO.get(evento.get('colorId', '9'), 'azul')
    
    mensagem = f'✏️ *Editando: {titulo}*\n'
    mensagem += f'📅 {hora_atual}\n'
    mensagem += f'🎨 Cor: {cor_atual}\n\n'
    mensagem += 'O que deseja alterar?\n\n'
    mensagem += '1️⃣ *Horário*\n'
    mensagem += '2️⃣ *Título*\n'
    mensagem += '3️⃣ *Cor*\n\n'
    mensagem += 'Responda: *1*, *2* ou *3*'
    
    return mensagem


def processar_escolha_edicao(phone_number, escolha):
    """Processa escolha do campo a editar (1=horário, 2=título, 3=cor)"""
    
    # [NOVO] Busca do SessionManager
    sessao = session_manager.get(phone_number, 'edicao')
    
    if not sessao or sessao['etapa'] != 'escolher_campo':
        return None
    
    evento = sessao['evento_selecionado']
    
    if escolha == '1':
        sessao['campo_para_editar'] = 'horario'
        sessao['etapa'] = 'aguardar_valor'
        session_manager.set(phone_number, 'edicao', sessao)
        hora_atual = formatar_data_br(evento['start'])
        return f'⏰ *Alterar horário*\nAtual: {hora_atual}\n\nQual o novo horário?\n_(ex: 16h, 14:30, 9h da manhã)_'
    
    elif escolha == '2':
        sessao['campo_para_editar'] = 'titulo'
        sessao['etapa'] = 'aguardar_valor'
        session_manager.set(phone_number, 'edicao', sessao)
        titulo_atual = evento.get('summary', 'Sem título')
        return f'📝 *Alterar título*\nAtual: {titulo_atual}\n\nQual o novo título?'
    
    elif escolha == '3':
        sessao['campo_para_editar'] = 'cor'
        sessao['etapa'] = 'aguardar_valor'
        session_manager.set(phone_number, 'edicao', sessao)
        cor_atual = CORES_INVERTIDO.get(evento.get('colorId', '9'), 'azul')
        return f'🎨 *Alterar cor*\nAtual: {cor_atual}\n\nOpções: vermelho, laranja, amarelo, verde, azul, roxo\n\nQual a nova cor?'
    
    else:
        return '❌ Opção inválida. Responda *1* (horário), *2* (título) ou *3* (cor).'


def aplicar_edicao(phone_number, valor_informado):
    """Aplica a edição no Google Calendar"""
    
    # [NOVO] Busca do SessionManager
    sessao = session_manager.get(phone_number, 'edicao')
    
    if not sessao or sessao['etapa'] != 'aguardar_valor':
        return None
    
    evento = sessao['evento_selecionado']
    campo = sessao['campo_para_editar']
    event_id = evento['id']
    
    try:
        # Busca evento atual no Google
        evento_atual = service.events().get(
            calendarId=CAL_ID_EVENTOS,
            eventId=event_id
        ).execute()
        
        if campo == 'horario':
            nova_hora, novo_minuto = extrair_hora_de_string(valor_informado)
            
            if nova_hora is None:
                return '❌ Horário não reconhecido. Use formato como *15h*, *15:30* ou *9h da manhã*.'
            
            # Extrai data atual do evento e aplica nova hora
            start_atual = evento_atual['start']
            if 'dateTime' in start_atual:
                data_iso = start_atual['dateTime']
                if '+' in data_iso:
                    data_limpa = data_iso[:data_iso.rfind('+')]
                else:
                    data_limpa = data_iso[:19]
                data_base = datetime.fromisoformat(data_limpa)
            else:
                # Evento de dia inteiro, converte para com hora
                data_base = datetime.strptime(start_atual['date'], '%Y-%m-%d')
            
            # Aplica nova hora
            nova_data = data_base.replace(hour=nova_hora, minute=novo_minuto or 0)
            
            # Calcula duração original para manter
            if 'dateTime' in evento_atual['end']:
                end_iso = evento_atual['end']['dateTime']
                if '+' in end_iso:
                    end_limpa = end_iso[:end_iso.rfind('+')]
                else:
                    end_limpa = end_iso[:19]
                end_dt = datetime.fromisoformat(end_limpa)
                duracao = end_dt - data_base
            else:
                duracao = timedelta(hours=1)
            
            novo_end = nova_data + duracao
            
            evento_atual['start']['dateTime'] = nova_data.isoformat()
            evento_atual['end']['dateTime'] = novo_end.isoformat()
            
            # Remove date se existia
            if 'date' in evento_atual['start']:
                del evento_atual['start']['date']
            if 'date' in evento_atual['end']:
                del evento_atual['end']['date']
            
            resultado = f"⏰ Horário atualizado: {nova_data.strftime('%H:%M')}"
            
        elif campo == 'titulo':
            titulo_antigo = evento_atual.get('summary', '')
            evento_atual['summary'] = valor_informado.title()
            resultado = f"📝 Título atualizado:\n*{titulo_antigo}* → *{valor_informado.title()}*"
            
        elif campo == 'cor':
            cor_nome = valor_informado.lower().strip()
            if cor_nome not in CORES:
                return f'❌ Cor inválida. Opções: {", ".join(CORES.keys())}'
            
            cor_antiga = CORES_INVERTIDO.get(evento_atual.get('colorId', '9'), 'azul')
            evento_atual['colorId'] = CORES[cor_nome]
            resultado = f"🎨 Cor atualizada: {cor_antiga} → {cor_nome}"
        
        # Aplica update no Google Calendar
        service.events().update(
            calendarId=CAL_ID_EVENTOS,
            eventId=event_id,
            body=evento_atual
        ).execute()
        
        # [NOVO] Limpa sessão do Redis
        session_manager.delete(phone_number, 'edicao')
        
        logger.info("Edição aplicada", extra={
            "user": phone_number,
            "event_id": event_id,
            "campo": campo
        })
        
        return f'✅ *Edição concluída!*\n\n{resultado}'
        
    except Exception as e:
        logger.error("Erro ao editar", extra={"user": phone_number, "error": str(e)})
        return '❌ Erro ao aplicar edição. Tente novamente.'

# ==========================================
# SISTEMA DE CANCELAMENTO (ATUALIZADO PARA REDIS)
# ==========================================

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
        
        # [NOVO] Usa SessionManager
        session_manager.set(phone_number, 'cancelamento', {
            'eventos': eventos
        })
        
        mensagem = '🗑️ *Eventos dos próximos 7 dias:*\n\n'
        
        for i, evento in enumerate(eventos, 1):
            titulo = evento.get('summary', 'Sem título')
            data_formatada = formatar_data_br(evento['start'])
            mensagem += f"{i}. *{titulo}*\n   📅 {data_formatada}\n\n"
        
        mensagem += 'Para cancelar, responda:\n*cancelar número*\n_(ex: cancelar 2)_'
        
        return mensagem
        
    except Exception as e:
        logger.error("Erro ao listar cancelamento", extra={"user": phone_number, "error": str(e)})
        return '❌ Erro ao buscar eventos. Tente novamente.'


def confirmar_cancelamento(phone_number, numero_escolhido):
    """Cancela o evento escolhido pelo número"""
    
    # [NOVO] Busca do SessionManager
    sessao = session_manager.get(phone_number, 'cancelamento')
    
    if not sessao:
        return '⚠️ Lista expirada. Envie *cancelar* para ver os eventos novamente.'
    
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
        
        # [NOVO] Limpa sessão
        session_manager.delete(phone_number, 'cancelamento')
        
        logger.info("Evento cancelado", extra={
            "user": phone_number,
            "event_id": evento['id'],
            "titulo": titulo_removido
        })
        
        return f'✅ *Cancelado com sucesso!*\n\n🗑️ {titulo_removido}\n📅 {data_removida}'
        
    except Exception as e:
        logger.error("Erro ao cancelar", extra={"user": phone_number, "error": str(e)})
        return '❌ Erro ao cancelar. O evento pode já ter sido removido.'

# ==========================================
# RESUMO DA SEMANA
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
        
        eventos_por_dia = {}
        
        for evento in eventos:
            start = evento['start']
            
            if 'date' in start:
                data_key = start['date']
            else:
                data_iso = start['dateTime'][:10]
                data_key = data_iso
            
            if data_key not in eventos_por_dia:
                eventos_por_dia[data_key] = []
            
            eventos_por_dia[data_key].append(evento)
        
        datas_ordenadas = sorted(eventos_por_dia.keys())
        
        mensagem = '📅 *Sua agenda - Próximos 7 dias*\n'
        mensagem += f'_{agora.strftime("%d/%m/%Y")} a {(agora + timedelta(days=7)).strftime("%d/%m/%Y")}_\n\n'
        
        for data_str in datas_ordenadas:
            eventos_do_dia = eventos_por_dia[data_str]
            
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
            
            for evento in eventos_do_dia:
                titulo = evento.get('summary', 'Sem título')
                cor_id = evento.get('colorId', '9')
                emoji = emoji_por_cor(cor_id)
                
                if 'date' in evento['start']:
                    hora_str = "dia todo"
                else:
                    hora = datetime.fromisoformat(evento['start']['dateTime'][:19])
                    hora_str = hora.strftime('%H:%M')
                
                mensagem += f"  {emoji} {hora_str} — {titulo}\n"
            
            mensagem += "\n"
        
        total = len(eventos)
        mensagem += f"📊 *Total: {total} evento{'s' if total > 1 else ''}*"
        
        return mensagem
        
    except Exception as e:
        logger.error("Erro no resumo semana", extra={"error": str(e)})
        return '❌ Erro ao buscar agenda. Tente novamente.'

# ==========================================
# FUNÇÕES ORIGINAIS
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
        logger.error("Twilio não configurado")
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
        logger.info("WhatsApp enviado", extra={"to": numero_limpo, "sid": msg.sid})
        return True
    except Exception as e:
        logger.error("Erro Twilio", extra={"error": str(e), "to": numero})
        return False

def verificar_lembretes():
    logger.info("Thread lembretes iniciada")
    while True:
        try:
            agora = agora_sp()
            time_min = agora.isoformat()
            time_max = (agora + timedelta(minutes=2)).isoformat()
            
            events = service.events().list(
                calendarId=CAL_ID_LEMBRETES,
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy='startTime',
                q='[LEMBRETE]'
            ).execute()
            
            for event in events.get('items', []):
                event_id = event.get('id')
                if event_id in lembretes_enviados:
                    continue
                
                titulo = event.get('summary', '').replace('[LEMBRETE] ', '')
                descricao = event.get('description', '')
                numero = descricao.replace('Numero: ', '').strip()
                
                mensagem = f"⏰ *Lembrete!*\n\n*{titulo}*\n🕐 {agora.strftime('%H:%M')}"
                
                logger.info("Enviando lembrete", extra={"titulo": titulo, "to": numero})
                if enviar_whatsapp(numero, mensagem):
                    lembretes_enviados.add(event_id)
                    
        except Exception as e:
            logger.error("Erro na thread de lembretes", extra={"error": str(e)})
        
        time.sleep(60)

threading.Thread(target=verificar_lembretes, daemon=True).start()

# ==========================================
# WEBHOOK PRINCIPAL (COM RATE LIMITING)
# ==========================================

@app.route("/webhook", methods=['POST'])
@limiter.limit("30 per minute")  # [NOVO] Proteção contra flood
def webhook():
    msg = request.values.get('Body', '').strip()
    numero = request.values.get('From', '')
    resp = MessagingResponse()
    
    msg_lower = msg.lower()
    
    # [NOVO] Logging estruturado de cada interação
    logger.info("Webhook recebido", extra={
        "user": numero,
        "message_text": msg[:50],  # ✅ CORRIGIDO: "msg" -> "message_text"
        "ip": request.remote_addr
    })
    
    # ==========================================
    # VERIFICAÇÃO DE SESSÕES ATIVAS PRIMEIRO
    # ==========================================
    
    # Verifica se usuário está em processo de edição
    sessao_edicao = session_manager.get(numero, 'edicao')
    if sessao_edicao:
        # Se está aguardando escolha do campo (1, 2 ou 3)
        if sessao_edicao['etapa'] == 'escolher_campo':
            if msg_lower in ['1', '2', '3']:
                resposta = processar_escolha_edicao(numero, msg_lower)
                resp.message(resposta)
                return str(resp)
            else:
                resp.message('❌ Opção inválida. Responda *1* (horário), *2* (título) ou *3* (cor).')
                return str(resp)
        
        # Se está aguardando o valor (novo horário/título/cor)
        elif sessao_edicao['etapa'] == 'aguardar_valor':
            resposta = aplicar_edicao(numero, msg)
            resp.message(resposta)
            return str(resp)
    
    # ==========================================
    # COMANDOS DE AGENDA
    # ==========================================
    
    params_agenda = interpretar_comando_agenda(msg_lower)
    if params_agenda:
        resposta = resumo_dia_especifico(**params_agenda)
        resp.message(resposta)
        return str(resp)
    
    # ==========================================
    # COMANDOS DE EDIÇÃO
    # ==========================================
    
    if msg_lower == 'editar':
        resposta = listar_eventos_para_editar(numero)
        resp.message(resposta)
        return str(resp)
    
    match_editar_numero = re.match(r'^editar\s+(\d+)$', msg_lower)
    if match_editar_numero:
        numero_escolhido = int(match_editar_numero.group(1))
        resposta = iniciar_edicao_evento(numero, numero_escolhido)
        resp.message(resposta)
        return str(resp)
    
    # ==========================================
    # COMANDOS EXISTENTES
    # ==========================================
    
    if msg_lower in ['agenda semana', 'agenda da semana', 'minha semana', 'próximos 7 dias']:
        resposta = resumo_semana()
        resp.message(resposta)
        return str(resp)
    
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
    
    if msg_lower in ['ajuda', 'help']:
        resp.message("""🤖 *Bot de Agenda*

*Eventos:*
• reunião amanhã 15h
• médico segunda 14:30 vermelho

*Lembretes:*
• lembrete daqui a 5 minutos
• lembrete pagar conta amanhã 15h

*Ver Agenda:*
• agenda hoje
• agenda amanhã
• agenda segunda (ou terça, quarta...)
• agenda 15/03
• agenda semana

*Editar:*
• editar (lista eventos)
• editar 2 (escolhe evento #2)
• depois escolhe: 1=horário, 2=título, 3=cor

*Cancelar:*
• cancelar (lista eventos)
• cancelar 2 (cancela o #2)

*Cores:* vermelho, laranja, amarelo, verde, azul, roxo""")
        return str(resp)
    
    # Criar evento ou lembrete
    if not (p := parse(msg)):
        resp.message("❓ Não entendi. Envie 'ajuda' para exemplos.")
        return str(resp)
    
    titulo, inicio, eh_lembrete, cor = p
    
    logger.info("Criando evento", extra={
        "user": numero,
        "titulo": titulo,
        "inicio": inicio.isoformat(),
        "eh_lembrete": eh_lembrete
    })
    
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
            logger.info("Lembrete criado", extra={"event_id": ev['id']})
            resp.message(f"""⏰ *Lembrete agendado!*

*{titulo}*
📅 {inicio.strftime('%d/%m/%Y %H:%M')}

💬 Te aviso na hora!""")
        except Exception as e:
            logger.error("Erro ao criar lembrete", extra={"error": str(e)})
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
            logger.info("Evento criado", extra={"event_id": ev['id']})
            emoji_cor = emoji_por_cor(cor)
            resp.message(f"{emoji_cor} 📅 *{titulo}*\n📆 {inicio.strftime('%d/%m/%Y %H:%M')}\n\n🔗 {ev.get('htmlLink')}")
        except Exception as e:
            logger.error("Erro ao criar evento", extra={"error": str(e)})
            resp.message(f"❌ Erro: {str(e)[:100]}")
    
    return str(resp)

@app.route("/")
def health():
    agora = agora_sp()
    redis_status = "connected" if (redis_client and redis_client.ping()) else "disconnected"
    
    return jsonify({
        "status": "ok",
        "hora": agora.strftime('%d/%m %H:%M:%S'),
        "twilio": twilio_client is not None,
        "redis": redis_status,
        "version": "2.0.0-redis"
    })

# [NOVO] Endpoint para testar Redis
@app.route("/test-redis")
def test_redis():
    try:
        if not redis_client:
            return jsonify({"status": "error", "message": "Redis não configurado"}), 500
        
        redis_client.set('test', 'ok', ex=10)
        value = redis_client.get('test')
        
        # Testa sessão
        session_manager.set('+5511999999999', 'test', {"foo": "bar"})
        sessao = session_manager.get('+5511999999999', 'test')
        session_manager.delete('+5511999999999', 'test')
        
        return jsonify({
            "status": "ok",
            "redis_connection": True,
            "test_value": value,
            "session_test": sessao
        })
    except Exception as e:
        logger.error("Teste Redis falhou", extra={"error": str(e)})
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
