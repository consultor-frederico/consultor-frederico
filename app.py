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

# Cadastro Simples de Usuários
USUARIOS_PERMITIDOS = {
    "fred": "12345",
    "cliente_teste": "abcde"
}

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
    except: return "[Erro na leitura]"

def conectar_google():
    try:
        if "google_credentials" in st.secrets:
            info_chaves = json.loads(st.secrets["google_credentials"]["json_data"])
            creds = Credentials.from_service_account_info(info_chaves, scopes=SCOPES)
        else:
            creds = Credentials.from_service_account_file('credentials.json', scopes=SCOPES)
        return gspread.authorize(creds), build('calendar', 'v3', credentials=creds)
    except: return None, None

def consultar_ia(mensagem, sistema, temperatura=0.3):
    try:
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {MINHA_CHAVE}", "Content-Type": "application/json"}
        dados = {"model": "llama-3.3-70b-versatile", "messages": [{"role": "system", "content": sistema}, {"role": "user", "content": mensagem}], "temperature": temperatura}
        resp = requests.post(url, headers=headers, json=dados).json()
        return resp['choices'][0]['message']['content']
    except: return "IA temporariamente indisponível."

def buscar_horarios_livres(service_calendar):
    sugestoes = []
    dia_foco = datetime.now() + timedelta(days=1)
    while len(sugestoes) < 12:
        if dia_foco.weekday() >= 5 or dia_foco.strftime("%d/%m") in FERIADOS_NACIONAIS:
            dia_foco += timedelta(days=1); continue
        inicio_iso = dia_foco.replace(hour=9, minute=0, second=0).isoformat() + 'Z'
        fim_iso = dia_foco.replace(hour=18, minute=0, second=0).isoformat() + 'Z'
        events_result = service_calendar.events().list(calendarId=ID_AGENDA, timeMin=inicio_iso, timeMax=fim_iso, singleEvents=True, orderBy='startTime').execute()
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
        evento = {'summary': f'Cálculo: {nome}', 'description': f'WhatsApp: {tel}', 'start': {'dateTime': data_c.isoformat(), 'timeZone': 'America/Sao_Paulo'}, 'end': {'dateTime': (data_c + timedelta(hours=1)).isoformat(), 'timeZone': 'America/Sao_Paulo'}}
        service_calendar.events().insert(calendarId=ID_AGENDA, body=evento).execute()
        return "Agendado"
    except: return "Erro Agenda"

def salvar_na_planilha(client_sheets, dados):
    try:
        sh = client_sheets.open(NOME_PLANILHA_GOOGLE); sheet = sh.sheet1
        if not sheet.get_all_values():
            sheet.append_row(["Data", "Logado", "Tipo", "Nome/Razão", "Responsável", "Contato", "Email", "CNPJ", "Horário", "Serviço", "Data Prazo", "Relato Inicial", "IA Inicial", "Relato Comp.", "IA Resposta Comp.", "Arquivo", "Análise Profunda Fred", "Status"])
        linha = [
            dados['data_hora'], dados['usuario_logado'], dados['tipo_usuario'], dados['nome'], dados.get('resp', ''), dados['telefone'], dados['email'], dados.get('cnpj', ''),
            dados['melhor_horario'], dados['servico'], dados.get('prazo', ''), dados['relato_inicial'], dados['ia_inicial'], 
            dados['complemento_relato'], dados['ia_resposta_complementar'], dados['nome_arquivo'], dados['analise_profunda'], dados['status_agenda']
        ]
        sheet.append_row(linha)
        return True
    except: return False

def tela_login():
    col_logo, col_text = st.columns([1, 4])
    with col_logo: st.markdown("<h1 style='text-align: center; margin-top: 5px;'>🔐</h1>", unsafe_allow_html=True)
    with col_text: st.markdown("# Área Restrita\nEfetue o login para acessar o sistema.")
    usuario = st.text_input("Usuário")
    senha = st.text_input("Senha", type="password")
    if st.button("Entrar"):
        if USUARIOS_PERMITIDOS.get(usuario) == senha:
            st.session_state.logado = True
            st.session_state.usuario_logado = usuario
            st.rerun()
        else: st.error("Usuário ou senha incorretos.")

# --- APLICAÇÃO PRINCIPAL ---
def main():
    # ESCONDER ÍCONES E MENUS DO STREAMLIT
    st.markdown("""
        <style>
        #MainMenu {visibility: hidden;}
        footer {visibility: hidden;}
        header {visibility: hidden;}
        .stAppDeployButton {display:none;}
        </style>
        """, unsafe_allow_html=True)

    if 'logado' not in st.session_state: st.session_state.logado = False
    if 'fase' not in st.session_state: st.session_state.fase = 1
    if 'dados_form' not in st.session_state: st.session_state.dados_form = {}
    if 'ia_inicial' not in st.session_state: st.session_state.ia_inicial = ""
    if 'ia_resposta_complementar' not in st.session_state: st.session_state.ia_resposta_complementar = "Sem complemento"
    if 'relato_complementar' not in st.session_state: st.session_state.relato_complementar = "Não enviado"
    if 'conteudo_arquivo' not in st.session_state: st.session_state.conteudo_arquivo = ""
    if 'nome_arquivo' not in st.session_state: st.session_state.nome_arquivo = "Não enviado"

    if not st.session_state.logado:
        tela_login()
        return

    client_sheets, service_calendar = conectar_google()

    col_logo, col_text = st.columns([1, 4])
    with col_logo: st.markdown("<h1 style='text-align: center; margin-top: 5px;'>📟</h1>", unsafe_allow_html=True)
    with col_text:
        st.markdown(f"<h1 style='margin-bottom: -15px;'>Fred Novotny</h1>", unsafe_allow_html=True)
        st.markdown(f"<p style='color: gray;'>Sessão: {st.session_state.usuario_logado}</p>", unsafe_allow_html=True)
    
    if st.sidebar.button("Logout"):
        st.session_state.logado = False
        st.rerun()

    st.divider()

    if st.session_state.fase == 1:
        st.subheader("1. Identificação e Caso")
        d = st.session_state.dados_form
        tipo = st.radio("Perfil:", ["Advogado", "Empresa", "Colaborador"], horizontal=True, index=["Advogado", "Empresa", "Colaborador"].index(d.get("tipo", "Advogado")))
        
        col1, col2 = st.columns(2)
        if tipo == "Empresa":
            nome = col1.text_input("Razão Social", value=d.get("nome", ""))
            cnpj = col2.text_input("CNPJ", key="cnpj_input", on_change=formatar_cnpj_callback, placeholder="00.000.000/0000-00", value=d.get("cnpj", ""))
            resp = st.text_input("Responsável", value=d.get("resp", ""))
            email = st.text_input("E-mail", value=d.get("email", ""))
            tel = st.text_input("WhatsApp", key="tel_input", on_change=formatar_tel_callback, value=d.get("tel", ""))
        else:
            nome = col1.text_input("Nome Completo", value=d.get("nome", ""))
            email = col2.text_input("E-mail", value=d.get("email", ""))
            tel = st.text_input("WhatsApp", key="tel_input", on_change=formatar_tel_callback, value=d.get("tel", ""))
            cnpj = ""; resp = nome
        
        opcoes = ["Liquidação", "Iniciais", "Impugnação", "Rescisão", "Horas Extras", "Outros"] if tipo == "Advogado" else ["Rescisão", "Horas Extras", "Outros"]
        servico = st.selectbox("Serviço:", opcoes, index=opcoes.index(d.get("servico")) if d.get("servico") in opcoes else 0)
        
        c_adm, c_sai = st.columns(2)
        adm = c_adm.text_input("Admissão", key="adm_input", on_change=formatar_data_adm_callback, value=d.get("adm", ""))
        sai = c_sai.text_input("Saída", key="sai_input", on_change=formatar_data_sai_callback, value=d.get("sai", ""))
        
        col_sal, col_prazo = st.columns(2)
        salario = col_sal.text_input("Salário Base", key="sal_input", on_change=formatar_salario_callback, value=d.get("salario", ""))
        
        # Sugestão 1: Verificação de Prazos (Exclusivo Advogado)
        if tipo == "Advogado":
            prazo = col_prazo.text_input("Data Prazo/Citação", key="prazo_input", on_change=formatar_data_prazo_callback, value=d.get("prazo", ""))
        else:
            prazo = ""
        
        relato = st.text_area("Resumo da Demanda:", value=d.get("relato", ""))

        if st.button("💬 Analisar Solicitação"):
            if not nome or not tel: st.warning("Preencha Nome e WhatsApp.")
            else:
                st.session_state.dados_form.update({"nome": nome, "resp": resp, "tel": tel, "email": email, "cnpj": cnpj, "tipo": tipo, "servico": servico, "adm": adm, "sai": sai, "salario": salario, "relato": relato, "prazo": prazo})
                with st.spinner("Analisando..."):
                    p = f"""
                    Você é o assistente do Consultor Frederico.
                    Usuário: {nome} | Perfil: {tipo} | Serviço: {servico}
                    Dados: Adm {adm}, Saída {sai}, Salário {salario}, Prazo {prazo}.
                    
                    REGRAS:
                    1. CUMPRIMENTE: Use 'Dr./Dra. {nome}' se Advogado. Use 'Sr./Sra. {nome}' para demais.
                    2. NÃO descreva raciocínio interno. Vá direto à saudação.
                    3. Confirme entendimento e peça complemento se vago.
                    """
                    st.session_state.ia_inicial = consultar_ia(p, "Assistente Jurídico.")
                    st.session_state.fase = 2; st.rerun()

    if st.session_state.fase == 2:
        st.subheader("2. Confirmação")
        st.info(st.session_state.ia_inicial)
        opcao = st.radio("Deseja complementar?", ["Apenas agenda", "Relato complementar", "Enviar documentos"], horizontal=True)
        if opcao == "Relato complementar":
            rel_comp = st.text_area("Complemento:", value=st.session_state.relato_complementar if st.session_state.relato_complementar != "Não enviado" else "")
            if st.button("Salvar Complemento"): 
                st.session_state.relato_complementar = rel_comp
                p_comp = f"O usuário {st.session_state.dados_form['nome']} ({st.session_state.dados_form['tipo']}) complementou: {rel_comp}. Responda se entendeu com o tratamento Dr/Dra ou Sr/Sra."
                st.session_state.ia_resposta_complementar = consultar_ia(p_comp, "Assistente Jurídico.")
                st.rerun()
        elif opcao == "Enviar documentos":
            arquivo = st.file_uploader("Anexar PDF", type=["pdf"])
            if arquivo:
                st.session_state.nome_arquivo = arquivo.name
                st.session_state.conteudo_arquivo = ler_conteudo_arquivo(arquivo)
                st.success("Pronto.")
        
        c1, c2 = st.columns(2)
        if c1.button("✅ Ir para Agenda"): st.session_state.fase = 4; st.rerun()
        if c2.button("❌ Refazer"): st.session_state.fase = 1; st.rerun()

    if st.session_state.fase == 4:
        st.subheader("🗓️ Agendamento")
        horarios = buscar_horarios_livres(service_calendar)
        horario = st.selectbox("Horário:", horarios)
        if st.button("✅ Finalizar"):
            with st.spinner("Processando..."):
                d = st.session_state.dados_form
                # Sugestão 2: Honorários sugeridos e análise de riscos
                p_fred = f"""
                PERITO: Analise INTEGRALMENTE: Relato 1: {d['relato']} | Relato 2: {st.session_state.relato_complementar} | Doc: {st.session_state.conteudo_arquivo}.
                Serviço: {d['servico']} | Salário: {d['salario']} | Prazo: {d['prazo']}.
                Dê ao Fred: 1. Dificuldade (1-10), 2. Verbas, 3. Honorários sugeridos (valor mercado), 4. Riscos/Urgência.
                """
                analise = consultar_ia(p_fred, "Perito Contábil Sênior")
                status = criar_evento_agenda(service_calendar, horario, d['nome'], d['tel'], d['servico'])
                salvar_na_planilha(client_sheets, {**d, "usuario_logado": st.session_state.usuario_logado, "data_hora": datetime.now().strftime("%d/%m %H:%M"), "melhor_horario": horario, "relato_inicial": d['relato'], "ia_inicial": st.session_state.ia_inicial, "complemento_relato": st.session_state.relato_complementar, "ia_resposta_complementar": st.session_state.ia_resposta_complementar, "nome_arquivo": st.session_state.nome_arquivo, "analise_profunda": analise, "status_agenda": status, "tipo_usuario": d['tipo'], "telefone": d['tel']})
                st.session_state.fase = 5; st.rerun()

    if st.session_state.fase == 5:
        st.balloons(); st.success("✅ Concluído!"); st.button("🔄 Novo", on_click=lambda: st.session_state.clear())

if __name__ == "__main__":
    main()
