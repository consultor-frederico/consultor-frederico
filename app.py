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
# Puxando as chaves de forma segura do painel de Secrets do Streamlit
MINHA_CHAVE = st.secrets["MINHA_CHAVE"]
ID_AGENDA = st.secrets["ID_AGENDA"]

st.set_page_config(page_title="Consultor Frederico - Cálculos", page_icon="🧮")

FERIADOS_NACIONAIS = ["01/01", "21/04", "01/05", "07/09", "12/10", "02/11", "15/11", "25/12"]
SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive', 'https://www.googleapis.com/auth/calendar']
NOME_PLANILHA_GOOGLE = 'Atendimento_Fred' 

# --- CSS PARA PROFISSIONALISMO (ESCONDE ÍCONES DE ADMIN) ---
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

def formatar_data_prazo_callback():
    val = st.session_state.prazo_input
    limpo = re.sub(r'\D', '', str(val))
    if len(limpo) == 8:
        st.session_state.prazo_input = f"{limpo[:2]}/{limpo[2:4]}/{limpo[4:]}"

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
    except: return "[Erro na leitura técnica]"

def conectar_google():
    try:
        # Busca o JSON das credenciais nas Secrets
        info_chaves = json.loads(st.secrets["google_credentials"]["json_data"])
        creds = Credentials.from_service_account_info(info_chaves, scopes=SCOPES)
        return gspread.authorize(creds), build('calendar', 'v3', credentials=creds)
    except Exception as e:
        return None, None

def consultar_ia(mensagem, sistema):
    try:
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {MINHA_CHAVE}", "Content-Type": "application/json"}
        # Modelo trocado para llama-3.1-8b-instant (mais rápido e estável)
        dados = {"model": "llama-3.1-8b-instant", "messages": [{"role": "system", "content": sistema}, {"role": "user", "content": mensagem}], "temperature": 0.3}
        resp = requests.post(url, headers=headers, json=dados).json()
        return resp['choices'][0]['message']['content']
    except: 
        return "Olá! Entendi perfeitamente sua demanda. Por favor, escolha um horário na próxima tela para conversarmos sobre o seu caso."

def buscar_horarios_livres(service_calendar):
    sugestoes = []
    dia_foco = datetime.now() + timedelta(days=1)
    while len(sugestoes) < 12:
        if dia_foco.weekday() >= 5 or dia_foco.strftime("%d/%m") in FERIADOS_NACIONAIS:
            dia_foco += timedelta(days=1); continue
        inicio_iso = dia_foco.replace(hour=9, minute=0, second=0).isoformat() + 'Z'
        fim_iso = dia_foco.replace(hour=18, minute=0, second=0).isoformat() + 'Z'
        try:
            events_result = service_calendar.events().list(calendarId=ID_AGENDA, timeMin=inicio_iso, timeMax=fim_iso, singleEvents=True, orderBy='startTime').execute()
            events = events_result.get('items', [])
            horas_ocupadas = [datetime.fromisoformat(e['start'].get('dateTime').replace('Z', '')).hour for e in events if 'dateTime' in e['start']]
        except:
            horas_ocupadas = []
            
        dia_txt = f"{dia_foco.strftime('%d/%m')} ({['Seg','Ter','Qua','Qui','Sex'][dia_foco.weekday()]})"
        for h in range(9, 18):
            if h != 12 and h not in horas_ocupadas:
                sugestoes.append(f"{dia_txt} às {h}:00")
        dia_foco += timedelta(days=1)
    return sugestoes[:15]

def criar_evento_agenda(service_calendar, horario_texto, nome, tel, servico):
    try:
        partes = horario_texto.split(" às ")
        data_pt, hora_pt = partes[0].split(" ")[0], partes[1]
        data_c = datetime.strptime(f"{data_pt}/{datetime.now().year} {hora_pt}", "%d/%m/%Y %H:%M")
        evento = {'summary': f'Cálculo: {nome} ({servico})', 'description': f'WhatsApp: {tel}', 'start': {'dateTime': data_c.isoformat(), 'timeZone': 'America/Sao_Paulo'}, 'end': {'dateTime': (data_c + timedelta(hours=1)).isoformat(), 'timeZone': 'America/Sao_Paulo'}}
        service_calendar.events().insert(calendarId=ID_AGENDA, body=evento).execute()
        return "Agendado"
    except: return "Erro Agenda"

def salvar_na_planilha(client_sheets, dados):
    try:
        sh = client_sheets.open(NOME_PLANILHA_GOOGLE); sheet = sh.sheet1
        if not sheet.get_all_values():
            sheet.append_row(["Data", "Tipo", "Nome", "WhatsApp", "Email", "Serviço", "IA Inicial", "Análise Profunda Fred", "Status"])
        linha = [dados['data_hora'], dados['tipo'], dados['nome'], dados['tel'], dados['email'], dados['servico'], dados['ia_inicial'], dados['analise_profunda'], dados['status_agenda']]
        sheet.append_row(linha)
        return True
    except: return False

# --- APLICAÇÃO PRINCIPAL ---
def main():
    if 'fase' not in st.session_state: st.session_state.fase = 1
    if 'dados_form' not in st.session_state: st.session_state.dados_form = {}
    if 'ia_inicial' not in st.session_state: st.session_state.ia_inicial = ""
    if 'relato_complementar' not in st.session_state: st.session_state.relato_complementar = "Não enviado"
    if 'conteudo_arquivo' not in st.session_state: st.session_state.conteudo_arquivo = ""

    client_sheets, service_calendar = conectar_google()

    col_logo, col_text = st.columns([1, 4])
    with col_logo: st.markdown("<h1 style='text-align: center; margin-top: 5px;'>📟</h1>", unsafe_allow_html=True)
    with col_text:
        st.markdown("<h1 style='margin-bottom: -15px;'>Frederico Novotny</h1>", unsafe_allow_html=True)
        st.markdown("<h3 style='color: gray;'>Consultor Trabalhista</h3>", unsafe_allow_html=True)
    st.divider()

    if st.session_state.fase == 1:
        st.subheader("1. Identificação")
        d = st.session_state.dados_form
        tipo = st.radio("Perfil:", ["Advogado", "Empresa", "Colaborador"], horizontal=True, 
                        index=["Advogado", "Empresa", "Colaborador"].index(d.get("tipo", "Advogado")))
        
        nome = st.text_input("Nome Completo / Razão Social", value=d.get("nome", ""))
        email = st.text_input("E-mail", value=d.get("email", ""))
        tel = st.text_input("WhatsApp", key="tel_input", on_change=formatar_tel_callback, value=d.get("tel", ""))
        
        opcoes = ["Liquidação", "Iniciais", "Impugnação", "Rescisão", "Horas Extras", "Outros"] if tipo == "Advogado" else ["Rescisão", "Horas Extras", "Outros"]
        servico = st.selectbox("Serviço:", opcoes, index=opcoes.index(d.get("servico")) if d.get("servico") in opcoes else 0)
        
        c_adm, c_sai = st.columns(2)
        adm = c_adm.text_input("Admissão", key="adm_input", on_change=formatar_data_adm_callback, value=d.get("adm", ""))
        sai = c_sai.text_input("Saída", key="sai_input", on_change=formatar_data_sai_callback, value=d.get("sai", ""))
        
        salario = st.text_input("Salário Base", key="sal_input", on_change=formatar_salario_callback, value=d.get("salario", ""))
        
        if tipo == "Advogado":
            prazo = st.text_input("Data Prazo/Citação", key="prazo_input", on_change=formatar_data_prazo_callback, value=d.get("prazo", ""))
        else: prazo = ""
        
        relato = st.text_area("Descreva sua demanda:", value=d.get("relato", ""))

        if st.button("💬 Analisar Solicitação"):
            if not nome or not tel: st.warning("Preencha Nome e WhatsApp.")
            else:
                st.session_state.dados_form.update({"nome": nome, "tel": tel, "email": email, "tipo": tipo, "servico": servico, "adm": adm, "sai": sai, "salario": salario, "relato": relato, "prazo": prazo})
                with st.spinner("Analisando..."):
                    p = f"Trate por Dr/Dra se Advogado ou Sr/Sra se demais. Usuário: {nome}, Serviço: {servico}. Datas: {adm} a {sai}. Salário: {salario}. Relato: {relato}. Confirme cordial."
                    st.session_state.ia_inicial = consultar_ia(p, "Assistente Jurídico Direto.")
                    st.session_state.fase = 2; st.rerun()

    if st.session_state.fase == 2:
        st.subheader("2. Confirmação")
        st.info(st.session_state.ia_inicial)
        opcao = st.radio("Deseja complementar?", ["Apenas seguir", "Adicionar detalhes", "Enviar PDF"], horizontal=True)
        if opcao == "Adicionar detalhes":
            rel_comp = st.text_area("Complemento:")
            if st.button("Salvar"): st.session_state.relato_complementar = rel_comp; st.success("OK")
        elif opcao == "Enviar PDF":
            arquivo = st.file_uploader("PDF", type=["pdf"])
            if arquivo: st.session_state.conteudo_arquivo = ler_conteudo_arquivo(arquivo); st.success("Recebido")
        
        c1, c2 = st.columns(2)
        if c1.button("✅ Agendar"): st.session_state.fase = 4; st.rerun()
        if c2.button("❌ Refazer"): st.session_state.fase = 1; st.rerun()

    if st.session_state.fase == 4:
        st.subheader("🗓️ Horário")
        horarios = buscar_horarios_livres(service_calendar)
        horario = st.selectbox("Escolha um horário:", horarios)
        if st.button("✅ Finalizar"):
            with st.spinner("Processando..."):
                d = st.session_state.dados_form
                p_fred = f"PERITO: Analise {d['relato']} e sugira honorários, dificuldade e riscos técnicos."
                analise = consultar_ia(p_fred, "Perito Sênior")
                status = criar_evento_agenda(service_calendar, horario, d['nome'], d['tel'], d['servico'])
                salvar_na_planilha(client_sheets, {**d, "data_hora": datetime.now().strftime("%d/%m %H:%M"), "melhor_horario": horario, "ia_inicial": st.session_state.ia_inicial, "analise_profunda": analise, "status_agenda": status})
                st.session_state.fase = 5; st.rerun()

    if st.session_state.fase == 5:
        st.balloons(); st.success("✅ Solicitação enviada! Frederico entrará em contato em breve."); st.button("🔄 Novo Atendimento", on_click=lambda: st.session_state.clear())

if __name__ == "__main__":
    main()
