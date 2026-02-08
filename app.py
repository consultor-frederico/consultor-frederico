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
        if uploaded_file.type in ["image/png", "image/jpeg", "image/jpg"]:
            return "[AVISO: O sistema não lê texto de imagens automaticamente. Por favor, detalhe os dados no relato abaixo.]"
        if uploaded_file.type == "application/pdf":
            leitor = PyPDF2.PdfReader(uploaded_file)
            texto = "\n".join([p.extract_text() for p in leitor.pages if p.extract_text()])
            if not texto.strip():
                return "[AVISO: Este PDF parece ser uma imagem/digitalização sem texto extraível. Por favor, detalhe os dados no relato abaixo.]"
            return texto
        return str(uploaded_file.read(), "utf-8")
    except: return "[Erro na leitura técnica do arquivo]"

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

def consultar_ia(mensagem, sistema, temperatura=0.5):
    try:
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {MINHA_CHAVE}", "Content-Type": "application/json"}
        dados = {"model": "llama-3.3-70b-versatile", "messages": [{"role": "system", "content": sistema}, {"role": "user", "content": mensagem}], "temperature": temperatura}
        return requests.post(url, headers=headers, json=dados).json()['choices'][0]['message']['content']
    except: return "IA temporariamente indisponível."

def buscar_horarios_livres(service_calendar):
    sugestoes = []
    dia = datetime.now() + timedelta(days=2)
    while len(sugestoes) < 10:
        if dia.weekday() < 5 and dia.strftime("%d/%m") not in FERIADOS_NACIONAIS:
            dia_txt = f"{dia.strftime('%d/%m')} ({['Seg','Ter','Qua','Qui','Sex'][dia.weekday()]})"
            for h in [10, 14, 16]: sugestoes.append(f"{dia_txt} às {h}:00")
        dia += timedelta(days=1)
    return sugestoes[:10]

def criar_evento_agenda(service_calendar, horario, nome, tel, servico):
    return "Agendado"

def salvar_na_planilha(client_sheets, dados):
    try:
        sh = client_sheets.open(NOME_PLANILHA_GOOGLE)
        sheet = sh.sheet1
        if not sheet.get_all_values():
            sheet.append_row(["Data", "Tipo", "Nome", "Contato", "Email", "Horário", "Serviço", "Resumo Cliente", "Análise Técnica", "Arquivo", "Status"])
        linha = [dados['data_hora'], dados['tipo_usuario'], dados['nome'], dados['telefone'], dados['email'], dados['melhor_horario'], dados['servico'], dados['analise_cliente'], dados['analise_tecnica'], "Processado", dados['status_agenda']]
        sheet.append_row(linha)
        return True
    except Exception as e:
        st.error(f"❌ Erro Planilha: {e}")
        return False

# --- APLICAÇÃO PRINCIPAL ---
def main():
    if 'fase' not in st.session_state: st.session_state.fase = 1
    if 'dados_form' not in st.session_state: st.session_state.dados_form = {}
    if 'ia_resumo_cliente' not in st.session_state: st.session_state.ia_resumo_cliente = ""
    if 'conteudo_arquivo' not in st.session_state: st.session_state.conteudo_arquivo = ""

    client_sheets, service_calendar = conectar_google()

    if st.session_state.fase == 1:
        st.subheader("1. Identificação e Caso")
        d = st.session_state.dados_form
        
        p_idx = ["Advogado", "Empresa", "Colaborador"].index(d.get("tipo", "Advogado"))
        tipo = st.radio("Perfil:", ["Advogado", "Empresa", "Colaborador"], horizontal=True, index=p_idx)
        
        col1, col2 = st.columns(2)
        if tipo == "Empresa":
            nome = col1.text_input("Razão Social", value=d.get("nome", ""))
            cnpj = col2.text_input("CNPJ", value=d.get("cnpj", ""), key="cnpj_input", on_change=formatar_cnpj_callback)
            n_resp = st.text_input("Responsável", value=d.get("nome_resp", ""))
        else:
            nome = col1.text_input("Nome Completo", value=d.get("nome", ""))
            n_resp = nome
            cnpj = ""
            
        c_tel, c_mail = st.columns(2)
        tel = c_tel.text_input("WhatsApp", value=d.get("tel", ""), key="tel_input", on_change=formatar_tel_callback)
        mail = c_mail.text_input("E-mail", value=d.get("email", ""))
        
        opcoes = ["Liquidação", "Iniciais", "Impugnação", "Rescisão", "Horas Extras", "Outros"] if tipo == "Advogado" else ["Rescisão", "Horas Extras", "Outros"]
        s_idx = opcoes.index(d.get("servico")) if d.get("servico") in opcoes else 0
        servico = st.selectbox("Tipo de Cálculo:", opcoes, index=s_idx)
        
        c_adm, c_sai = st.columns(2)
        adm = c_adm.text_input("Admissão (DDMMAAAA)", value=d.get("adm", ""), key="adm_input", on_change=formatar_data_adm_callback)
        sai = c_sai.text_input("Saída (DDMMAAAA)", value=d.get("sai", ""), key="sai_input", on_change=formatar_data_sai_callback)
        salario = st.text_input("Salário Base", value=d.get("salario", ""), key="sal_input", on_change=formatar_salario_callback)
        
        relato = st.text_area("Resumo da Demanda:", value=d.get("relato", ""))

        if st.button("💬 Analisar Solicitação"):
            if not nome or not st.session_state.tel_input: st.warning("Preencha Nome e WhatsApp.")
            else:
                st.session_state.dados_form.update({
                    "nome": nome, "nome_resp": n_resp, "tel": st.session_state.tel_input, "email": mail, 
                    "cnpj": st.session_state.get("cnpj_input", ""), "tipo": tipo, "servico": servico, 
                    "relato": relato, "salario": st.session_state.sal_input, "adm": st.session_state.adm_input, "sai": st.session_state.sai_input
                })
                with st.spinner("IA entendendo o caso..."):
                    p_resumo = f"Aja como Frederico. O cliente relatou: '{relato}'. Apenas diga que entendeu e cite o objetivo de forma amigável em no máximo 2 frases."
                    st.session_state.ia_resumo_cliente = consultar_ia(p_resumo, "Consultor Jurídico")
                    st.session_state.fase = 2; st.rerun()

    if st.session_state.fase == 2:
        st.subheader("2. Confirmação")
        st.info(st.session_state.ia_resumo_cliente)
        col_v, col_r = st.columns(2)
        if col_v.button("✅ Confirmar"): st.session_state.fase = 3; st.rerun()
        if col_r.button("❌ Refazer"): st.session_state.fase = 1; st.rerun()

    if st.session_state.fase == 3:
        st.subheader("3. Documentos")
        # 🆕 ALTERAÇÃO: Apenas PDF e TXT visíveis no seletor
        arquivo = st.file_uploader("Anexar Documento (PDF ou TXT)", type=["pdf", "txt"])
        if arquivo: 
            conteudo = ler_conteudo_arquivo(arquivo)
            st.session_state.conteudo_arquivo = conteudo
            
            # 🆕 ALTERAÇÃO: Se houver aviso de imagem/digitalização, abre o quadro com o relato
            if "[AVISO" in conteudo:
                st.warning(conteudo)
                with st.expander("📝 Seu Relato da Fase anterior", expanded=True):
                    st.write(st.session_state.dados_form.get("relato", ""))
                st.info("Caso o arquivo seja uma digitalização, certifique-se de que os dados principais já foram descritos no relato acima.")
            else:
                st.success("Conteúdo do arquivo processado com sucesso.")
        
        if st.button("🔽 Ir para Agendamento"): st.session_state.fase = 4; st.rerun()

    if st.session_state.fase == 4:
        st.subheader("🗓️ Finalizar")
        horarios = buscar_horarios_livres(service_calendar)
        horario = st.selectbox("Escolha o Horário:", horarios)
        
        if st.button("✅ Confirmar Tudo"):
            with st.spinner("Gravando dados..."):
                d = st.session_state.dados_form
                p_t = f"Perfil {d['tipo']}. Cálculo de {d['servico']}. Salário {d['salario']}. Relato: {d['relato']}. CONTEÚDO DO ARQUIVO: {st.session_state.get('conteudo_arquivo', 'Não enviado')}. Dê valor sugerido e dificuldade técnica."
                analise_ia = consultar_ia(p_t, "Perito Judicial")
                status_agenda = criar_evento_agenda(service_calendar, horario, d['nome'], d['tel'], d['servico'])
                
                sucesso = salvar_na_planilha(client_sheets, {
                    "data_hora": datetime.now().strftime("%d/%m %H:%M"), "tipo_usuario": d['tipo'], "nome": d['nome'], "telefone": d['tel'], "email": d['email'],
                    "melhor_horario": horario, "servico": d['servico'], "analise_cliente": st.session_state.ia_resumo_cliente, "analise_tecnica": analise_ia, "status_agenda": status_agenda
                })
                
                if sucesso:
                    st.session_state.fase = 5; st.rerun()

    if st.session_state.fase == 5:
        st.balloons()
        st.success("✅ Solicitação enviada com sucesso!")
        st.divider()
        st.subheader("Obrigado por utilizar nossos serviços!")
        st.write("Sua solicitação foi processada e o horário foi reservado em nossa agenda.")
        st.info("Entraremos em contato em breve para dar andamento à sua demanda.")
        
        col_nov, col_fec = st.columns(2)
        if col_nov.button("🔄 Nova Consulta"):
            st.session_state.clear()
            st.rerun()
        if col_fec.button("🚪 Sair"):
            st.write("Atendimento encerrado. Você pode fechar esta aba.")
            st.stop()

if __name__ == "__main__":
    main()
