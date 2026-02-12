import streamlit as st
import datetime
import json
import random
import requests
import gspread
import re
import PyPDF2
from io import BytesIO
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from datetime import datetime, timedelta

# --- 🚨 CONFIGURAÇÕES 🚨 ---
MINHA_CHAVE = "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"
ID_AGENDA = "a497481e5251098078e6c68882a849680f499f6cef836ab976ffccdaad87689a@group.calendar.google.com"

st.set_page_config(page_title="Consultor Frederico - Cálculos", page_icon="🧮")

FERIADOS_NACIONAIS = ["01/01", "21/04", "01/05", "07/09", "12/10", "02/11", "15/11", "25/12"]

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets', 
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/calendar'
]

NOME_PLANILHA_GOOGLE = 'Atendimento_Fred' 

# --- CALLBACKS DE FORMATAÇÃO ---
def formatar_cnpj_callback():
    val = st.session_state.cnpj_input
    limpo = re.sub(r'\D', '', str(val))
    if len(limpo) == 14:
        st.session_state.cnpj_input = f"{limpo[:2]}.{limpo[2:5]}.{limpo[5:8]}/{limpo[8:12]}-{limpo[12:]}"

def formatar_data_adm_callback():
    val = st.session_state.adm_input
    limpo = re.sub(r'\D', '', str(val))
    if len(limpo) == 8:
        st.session_state.adm_input = f"{limpo[:2]}/{limpo[2:4]}/{limpo[4:]}"

def formatar_data_sai_callback():
    val = st.session_state.sai_input
    limpo = re.sub(r'\D', '', str(val))
    if len(limpo) == 8:
        st.session_state.sai_input = f"{limpo[:2]}/{limpo[2:4]}/{limpo[4:]}"

def formatar_salario_callback():
    val = st.session_state.sal_input
    if not val: return
    temp = val.replace("R$", "").replace(".", "").replace(",", ".").strip()
    try:
        valor_float = float(temp)
        st.session_state.sal_input = f"R$ {valor_float:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except: pass

def formatar_tel_callback():
    val = st.session_state.tel_input
    limpo = re.sub(r'\D', '', str(val))
    if len(limpo) == 11:
        st.session_state.tel_input = f"({limpo[:2]}) {limpo[2:7]}-{limpo[7:]}"
    elif len(limpo) == 10:
        st.session_state.tel_input = f"({limpo[:2]}) {limpo[2:6]}-{limpo[6:]}"

# --- FUNÇÕES DE SISTEMA ---
def ler_conteudo_arquivo(uploaded_file):
    if uploaded_file is None: return ""
    try:
        if uploaded_file.type == "application/pdf":
            leitor = PyPDF2.PdfReader(uploaded_file)
            texto = "\n".join([p.extract_text() for p in leitor.pages if p.extract_text()])
            return texto
        return str(uploaded_file.read(), "utf-8")
    except: return "[Erro na leitura]"

def conectar_google():
    try:
        if "google_credentials" in st.secrets:
            info_chaves = json.loads(st.secrets["google_credentials"]["json_data"])
            creds = Credentials.from_service_account_info(info_chaves, scopes=SCOPES)
        else:
            creds = Credentials.from_service_account_file('credentials.json', scopes=SCOPES)
        return gspread.authorize(creds), build('calendar', 'v3', credentials=creds)
    except Exception as e:
        st.error(f"❌ Erro de Conexão Google: {e}")
        return None, None

def consultar_ia(mensagem, sistema, temperatura=0.3):
    try:
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {MINHA_CHAVE}", "Content-Type": "application/json"}
        dados = {"model": "llama-3.1-8b-instant", "messages": [{"role": "system", "content": sistema}, {"role": "user", "content": mensagem}], "temperature": temperatura}
        resp = requests.post(url, headers=headers, json=dados)
        return resp.json()['choices'][0]['message']['content']
    except: return "IA temporariamente indisponível."

def buscar_horarios_livres(service_calendar):
    sugestoes = []
    dia_foco = datetime.now() + timedelta(days=1)
    while len(sugestoes) < 12:
        if dia_foco.weekday() >= 5 or dia_foco.strftime("%d/%m") in FERIADOS_NACIONAIS:
            dia_foco += timedelta(days=1); continue
        inicio_iso = dia_foco.replace(hour=9, minute=0, second=0).isoformat() + 'Z'
        fim_iso = dia_foco.replace(hour=18, minute=0, second=0).isoformat() + 'Z'
        events_result = service_calendar.events().list(calendarId=ID_AGENDA, timeMin=inicio_iso, timeMax=fim_iso, singleEvents=True).execute()
        events = events_result.get('items', [])
        horas_ocupadas = [datetime.fromisoformat(e['start'].get('dateTime').replace('Z', '')).hour for e in events if 'dateTime' in e['start']]
        dia_txt = f"{dia_foco.strftime('%d/%m')} ({['Seg','Ter','Qua','Qui','Sex'][dia_foco.weekday()]})"
        for h in range(9, 18):
            if h != 12 and h not in horas_ocupadas: sugestoes.append(f"{dia_txt} às {h}:00")
        dia_foco += timedelta(days=1)
    return sugestoes[:15]

def criar_evento_agenda(service_calendar, horario_texto, nome, tel, servico):
    try:
        partes = horario_texto.split(" às ")
        data_pt, hora_pt = partes[0].split(" ")[0], partes[1]
        data_c = datetime.strptime(f"{data_pt}/{datetime.now().year} {hora_pt}", "%d/%m/%Y %H:%M")
        evento = {
            'summary': f'Cálculo: {nome} ({servico})',
            'description': f'WhatsApp: {tel}',
            'start': {'dateTime': data_c.isoformat(), 'timeZone': 'America/Sao_Paulo'},
            'end': {'dateTime': (data_c + timedelta(hours=1)).isoformat(), 'timeZone': 'America/Sao_Paulo'},
        }
        service_calendar.events().insert(calendarId=ID_AGENDA, body=evento).execute()
        return "Agendado"
    except: return "Erro Agenda"

def salvar_na_planilha(client_sheets, dados):
    try:
        sh = client_sheets.open(NOME_PLANILHA_GOOGLE)
        sheet = sh.sheet1
        if not sheet.get_all_values():
            sheet.append_row(["Data", "Tipo", "Nome", "Contato", "Horário", "Serviço", "Resposta Inicial IA", "Complemento Cliente", "Nome do Arquivo", "Análise Total Frederico", "Status Agenda"])
        linha = [dados['data_hora'], dados['tipo_usuario'], dados['nome'], dados['telefone'], dados['melhor_horario'], dados['servico'], dados['ia_inicial'], dados['complemento_texto'], dados['nome_arquivo'], dados['analise_pericial'], dados['status_agenda']]
        sheet.append_row(linha)
        return True
    except: return False

# --- APLICAÇÃO PRINCIPAL ---
def main():
    if 'fase' not in st.session_state: st.session_state.fase = 1
    if 'dados_form' not in st.session_state: st.session_state.dados_form = {}
    if 'ia_resumo_cliente' not in st.session_state: st.session_state.ia_resumo_cliente = ""
    if 'conteudo_arquivo' not in st.session_state: st.session_state.conteudo_arquivo = ""
    if 'nome_arquivo' not in st.session_state: st.session_state.nome_arquivo = "Nenhum"

    client_sheets, service_calendar = conectar_google()

    col_logo, col_text = st.columns([1, 4])
    with col_logo: st.markdown("<h1 style='text-align: center; margin-top: 5px;'>📟</h1>", unsafe_allow_html=True)
    with col_text:
        st.markdown("<h1 style='margin-bottom: -15px; padding-bottom: 0;'>Frederico Novotny</h1>", unsafe_allow_html=True)
        st.markdown("<h3 style='color: gray; margin-top: 0; padding-top: 0;'>Consultor Trabalhista</h3>", unsafe_allow_html=True)
    st.divider()

    if st.session_state.fase == 1:
        st.subheader("1. Identificação")
        d = st.session_state.dados_form
        tipo = st.radio("Perfil:", ["Advogado", "Empresa", "Colaborador"], horizontal=True)
        col1, col2 = st.columns(2)
        nome = col1.text_input("Nome/Razão Social", value=d.get("nome", ""))
        cnpj = col2.text_input("CNPJ", key="cnpj_input", on_change=formatar_cnpj_callback)
        tel = st.text_input("WhatsApp", value=d.get("tel", ""), key="tel_input", on_change=formatar_tel_callback)
        servico = st.selectbox("Serviço:", ["Liquidação", "Iniciais", "Rescisão", "Horas Extras", "Outros"])
        
        if st.button("💬 Próximo"):
            st.session_state.dados_form.update({"nome": nome, "tel": st.session_state.tel_input, "tipo": tipo, "servico": servico})
            with st.spinner("Iniciando..."):
                p = f"Saúde cordialmente {nome} ({tipo}) que solicita {servico}. Seja direto."
                st.session_state.ia_resumo_cliente = consultar_ia(p, "Assistente Virtual.")
                st.session_state.fase = 2; st.rerun()

    if st.session_state.fase == 2:
        st.subheader("2. Confirmação")
        st.info(st.session_state.ia_resumo_cliente)
        col_v, col_r = st.columns(2)
        if col_v.button("✅ Confirmar"): st.session_state.fase = 3; st.rerun()
        if col_r.button("❌ Refazer"): st.session_state.fase = 1; st.rerun()

    if st.session_state.fase == 3:
        st.subheader("3. Documentos e Relato")
        st.warning("🔒 **LGPD:** Documentos são usados apenas para análise inicial e não serão salvos.")
        arquivo = st.file_uploader("Anexar PDF ou TXT", type=["pdf", "txt"])
        
        if arquivo:
            st.session_state.nome_arquivo = arquivo.name
            st.session_state.conteudo_arquivo = ler_conteudo_arquivo(arquivo)
            with st.spinner("Interpretando documento..."):
                p_int = f"Interprete brevemente este arquivo: {st.session_state.conteudo_arquivo[:1500]}. Diga o que identificou e peça ao usuário um breve relato complementar."
                st.chat_message("assistant").write(consultar_ia(p_int, "Perito Assistente."))
        
        relato = st.text_area("Breve relato do que você precisa:", placeholder="Ex: Preciso conferir as horas extras do arquivo acima...")
        
        if st.button("🗓️ Ir para Agendamento"):
            st.session_state.dados_form["relato_final"] = relato
            st.session_state.fase = 4; st.rerun()

    if st.session_state.fase == 4:
        st.subheader("4. Finalizar")
        horarios = buscar_horarios_livres(service_calendar)
        horario = st.selectbox("Horário:", horarios)
        if st.button("✅ Confirmar Tudo"):
            with st.spinner("Processando análise..."):
                d = st.session_state.dados_form
                p_t = f"Análise Pericial para Frederico: Relato: {d.get('relato_final', '')} | Doc: {st.session_state.conteudo_arquivo}"
                analise_total = consultar_ia(p_t, "Perito Trabalhista Sênior.")
                status = criar_evento_agenda(service_calendar, horario, d['nome'], d['tel'], d['servico'])
                salvar_na_planilha(client_sheets, {
                    "data_hora": datetime.now().strftime("%d/%m %H:%M"), "tipo_usuario": d['tipo'], "nome": d['nome'], "telefone": d['tel'],
                    "melhor_horario": horario, "servico": d['servico'], "ia_inicial": st.session_state.ia_resumo_cliente,
                    "complemento_texto": d.get("relato_final", ""), "nome_arquivo": st.session_state.nome_arquivo,
                    "analise_pericial": analise_total, "status_agenda": status
                })
                st.session_state.fase = 5; st.rerun()

    if st.session_state.fase == 5:
        st.balloons(); st.success("✅ Tudo pronto!"); st.button("🔄 Novo", on_click=lambda: st.session_state.clear())

if __name__ == "__main__":
    main()
