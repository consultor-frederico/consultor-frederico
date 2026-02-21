import streamlit as st
import datetime
import json
import requests
import gspread
import re
import PyPDF2
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from datetime import datetime, timedelta

# --- 🔐 CONFIGURAÇÕES VIA SECRETS ---
MINHA_CHAVE = st.secrets["MINHA_CHAVE"]
ID_AGENDA = st.secrets["ID_AGENDA"]

st.set_page_config(page_title="Consultor Frederico - Cálculos", page_icon="🧮")

# --- CSS PARA ESCONDER ÍCONES DE ADMIN ---
st.markdown("""
    <style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    .stAppDeployButton {display:none;}
    [data-testid="stStatusWidget"] {visibility: hidden;}
    .viewerBadge_container__1QSob {display:none !important;}
    </style>
    """, unsafe_allow_html=True)

# --- FUNÇÕES TÉCNICAS ---
def conectar_google():
    try:
        info_chaves = json.loads(st.secrets["google_credentials"]["json_data"])
        creds = Credentials.from_service_account_info(info_chaves, scopes=['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive', 'https://www.googleapis.com/auth/calendar'])
        return gspread.authorize(creds), build('calendar', 'v3', credentials=creds)
    except:
        return None, None

def consultar_ia(mensagem, sistema):
    try:
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {MINHA_CHAVE}", "Content-Type": "application/json"}
        # 🆕 TROCADO PARA O MODELO MAIS RÁPIDO E ESTÁVEL
        dados = {
            "model": "llama-3.1-8b-instant", 
            "messages": [{"role": "system", "content": sistema}, {"role": "user", "content": mensagem}], 
            "temperature": 0.3
        }
        resp = requests.post(url, headers=headers, json=dados).json()
        return resp['choices'][0]['message']['content']
    except Exception as e:
        return f"Olá! Entendi sua demanda. Por favor, escolha o melhor horário para conversarmos sobre os detalhes técnicos."

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

def buscar_horarios_livres(service_calendar):
    try:
        sugestoes = []
        dia_foco = datetime.now() + timedelta(days=1)
        while len(sugestoes) < 10:
            if dia_foco.weekday() >= 5:
                dia_foco += timedelta(days=1); continue
            dia_txt = f"{dia_foco.strftime('%d/%m')} ({['Seg','Ter','Qua','Qui','Sex'][dia_foco.weekday()]})"
            for h in [9, 10, 11, 14, 15, 16, 17]:
                sugestoes.append(f"{dia_txt} às {h}:00")
            dia_foco += timedelta(days=1)
        return sugestoes
    except:
        return ["Horários sob consulta via WhatsApp"]

def criar_evento_agenda(service_calendar, horario_texto, nome, tel, servico):
    return "Agendado"

def salvar_na_planilha(client_sheets, dados):
    try:
        sh = client_sheets.open('Atendimento_Fred')
        sheet = sh.sheet1
        linha = [dados['data_hora'], dados['tipo'], dados['nome'], dados['tel'], dados['email'], dados['servico'], dados['ia_inicial'], dados['status_agenda']]
        sheet.append_row(linha)
        return True
    except:
        return False

# --- APLICAÇÃO PRINCIPAL ---
def main():
    if 'fase' not in st.session_state: st.session_state.fase = 1
    if 'dados_form' not in st.session_state: st.session_state.dados_form = {}
    if 'ia_inicial' not in st.session_state: st.session_state.ia_inicial = ""

    client_sheets, service_calendar = conectar_google()

    st.markdown("<h1>📟 Frederico Novotny</h1>", unsafe_allow_html=True)
    st.markdown("<p style='color: gray;'>Consultor Trabalhista</p>", unsafe_allow_html=True)
    st.divider()

    if st.session_state.fase == 1:
        tipo = st.radio("Perfil:", ["Advogado", "Empresa", "Colaborador"], horizontal=True)
        nome = st.text_input("Nome / Razão Social")
        tel = st.text_input("WhatsApp", key="tel_input", on_change=formatar_tel_callback)
        email = st.text_input("E-mail")
        servico = st.selectbox("Serviço:", ["Liquidação", "Iniciais", "Impugnação", "Rescisão", "Outros"])
        
        c1, c2 = st.columns(2)
        adm = c1.text_input("Admissão (DDMMAAAA)", key="adm_input", on_change=formatar_data_adm_callback)
        sai = c2.text_input("Saída (DDMMAAAA)", key="sai_input", on_change=formatar_data_sai_callback)
        sal = st.text_input("Salário Base", key="sal_input", on_change=formatar_salario_callback)
        relato = st.text_area("Descreva sua demanda:")

        if st.button("💬 Analisar Solicitação"):
            if not nome or not tel: st.warning("Preencha Nome e WhatsApp.")
            else:
                st.session_state.dados_form.update({"nome": nome, "tel": tel, "email": email, "tipo": tipo, "servico": servico, "relato": relato})
                with st.spinner("Conectando..."):
                    p = f"Trate por Dr/Dra se Advogado ou Sr/Sra se demais. Usuário: {nome}, Serviço: {servico}. Datas: {adm}-{sai}. Relato: {relato}. Confirme cordial."
                    st.session_state.ia_inicial = consultar_ia(p, "Assistente Jurídico.")
                    st.session_state.fase = 2; st.rerun()

    if st.session_state.fase == 2:
        st.info(st.session_state.ia_inicial)
        if st.button("✅ Ir para Agenda"): st.session_state.fase = 4; st.rerun()
        if st.button("❌ Refazer"): st.session_state.fase = 1; st.rerun()

    if st.session_state.fase == 4:
        st.subheader("🗓️ Escolha o Horário")
        horarios = buscar_horarios_livres(service_calendar)
        horario = st.selectbox("Horários:", horarios)
        if st.button("✅ Finalizar"):
            d = st.session_state.dados_form
            salvar_na_planilha(client_sheets, {**d, "data_hora": datetime.now().strftime("%d/%m %H:%M"), "melhor_horario": horario, "ia_inicial": st.session_state.ia_inicial, "status_agenda": "Agendado"})
            st.session_state.fase = 5; st.rerun()

    if st.session_state.fase == 5:
        st.balloons(); st.success("✅ Solicitação enviada! Frederico entrará em contato."); st.button("🔄 Novo", on_click=lambda: st.session_state.clear())

if __name__ == "__main__":
    main()
