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
            dia_foco += timedelta(days=1)
            continue
        inicio_iso = dia_foco.replace(hour=9, minute=0, second=0).isoformat() + 'Z'
        fim_iso = dia_foco.replace(hour=18, minute=0, second=0).isoformat() + 'Z'
        events_result = service_calendar.events().list(
            calendarId=ID_AGENDA, timeMin=inicio_iso, timeMax=fim_iso,
            singleEvents=True, orderBy='startTime'
        ).execute()
        events = events_result.get('items', [])
        horas_ocupadas = []
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            if 'T' in start:
                h_inicio = datetime.fromisoformat(start.replace('Z', '')).hour
                horas_ocupadas.append(h_inicio)
        dia_txt = f"{dia_foco.strftime('%d/%m')} ({['Seg','Ter','Qua','Qui','Sex'][dia_foco.weekday()]})"
        for h in range(9, 18):
            if h == 12: continue 
            if h not in horas_ocupadas:
                sugestoes.append(f"{dia_txt} às {h}:00")
        dia_foco += timedelta(days=1)
    return sugestoes[:15]

def criar_evento_agenda(service_calendar, horario_texto, nome, tel, servico):
    try:
        partes = horario_texto.split(" às ")
        data_pt = partes[0].split(" ")[0]
        hora_pt = partes[1]
        ano_atual = datetime.now().year
        data_completa = datetime.strptime(f"{data_pt}/{ano_atual} {hora_pt}", "%d/%m/%Y %H:%M")
        start_time = data_completa.isoformat()
        end_time = (data_completa + timedelta(hours=1)).isoformat()
        evento = {
            'summary': f'Cálculo: {nome} ({servico})',
            'description': f'WhatsApp: {tel}\nSolicitação via Web App Frederico.',
            'start': {'dateTime': start_time, 'timeZone': 'America/Sao_Paulo'},
            'end': {'dateTime': end_time, 'timeZone': 'America/Sao_Paulo'},
        }
        service_calendar.events().insert(calendarId=ID_AGENDA, body=evento).execute()
        return "Agendado com Sucesso"
    except Exception as e:
        return f"Erro Agenda: {str(e)}"

def salvar_na_planilha(client_sheets, dados):
    try:
        sh = client_sheets.open(NOME_PLANILHA_GOOGLE)
        sheet = sh.sheet1
        if not sheet.get_all_values():
            sheet.append_row(["Data", "Tipo", "Nome", "Contato", "Horário", "Serviço", "Resposta Inicial IA", "Complemento Relato", "Nome do Arquivo", "Análise Profunda IA", "Status"])
        linha = [
            dados['data_hora'], dados['tipo_usuario'], dados['nome'], dados['telefone'], 
            dados['melhor_horario'], dados['servico'], dados['ia_inicial'], 
            dados['complemento_relato'], dados['nome_arquivo'], dados['analise_profunda'], dados['status_agenda']
        ]
        sheet.append_row(linha)
        return True
    except Exception as e:
        st.error(f"❌ Erro Planilha: {e}")
        return False

# --- APLICAÇÃO PRINCIPAL ---
def main():
    if 'fase' not in st.session_state: st.session_state.fase = 1
    if 'dados_form' not in st.session_state: st.session_state.dados_form = {}
    if 'ia_inicial' not in st.session_state: st.session_state.ia_inicial = ""
    if 'relato_complementar' not in st.session_state: st.session_state.relato_complementar = ""
    if 'conteudo_arquivo' not in st.session_state: st.session_state.conteudo_arquivo = ""
    if 'nome_arquivo' not in st.session_state: st.session_state.nome_arquivo = "Não enviado"

    client_sheets, service_calendar = conectar_google()

    col_logo, col_text = st.columns([1, 4])
    with col_logo: st.markdown("<h1 style='text-align: center; margin-top: 5px;'>📟</h1>", unsafe_allow_html=True)
    with col_text:
        st.markdown("<h1 style='margin-bottom: -15px; padding-bottom: 0;'>Frederico Novotny</h1>", unsafe_allow_html=True)
        st.markdown("<h3 style='color: gray; margin-top: 0; padding-top: 0;'>Consultor Trabalhista</h3>", unsafe_allow_html=True)
    st.divider()

    if st.session_state.fase == 1:
        st.subheader("1. Identificação e Caso")
        tipo = st.radio("Perfil:", ["Advogado", "Empresa", "Colaborador"], horizontal=True)
        col1, col2 = st.columns(2)
        nome = col1.text_input("Nome/Razão Social")
        cnpj = col2.text_input("CNPJ", key="cnpj_input", on_change=formatar_cnpj_callback)
        tel = st.text_input("WhatsApp", key="tel_input", on_change=formatar_tel_callback)
        servico = st.selectbox("Serviço:", ["Liquidação", "Iniciais", "Impugnação", "Rescisão", "Horas Extras", "Outros"])
        
        c_adm, c_sai = st.columns(2)
        adm = c_adm.text_input("Admissão (DDMMAAAA)", key="adm_input", on_change=formatar_data_adm_callback)
        sai = c_sai.text_input("Saída (DDMMAAAA)", key="sai_input", on_change=formatar_data_sai_callback)
        salario = st.text_input("Salário Base", key="sal_input", on_change=formatar_salario_callback)
        
        relato = st.text_area("Resumo da Demanda:")

        if st.button("💬 Analisar Solicitação"):
            if not nome or not st.session_state.tel_input: st.warning("Preencha Nome e WhatsApp.")
            else:
                st.session_state.dados_form.update({
                    "nome": nome, "tel": st.session_state.tel_input, "tipo": tipo, "servico": servico,
                    "adm": st.session_state.adm_input, "sai": st.session_state.sai_input, "salario": st.session_state.sal_input, "relato": relato
                })
                with st.spinner("Analisando..."):
                    p_resumo = f"""
                    Usuário {nome} ({tipo}) solicita {servico}. Relato: '{relato}'. 
                    Dados fornecidos: Admissão {st.session_state.adm_input}, Saída {st.session_state.sai_input}, Salário {st.session_state.sal_input}.
                    Confirme o entendimento. Se o relato for incompleto, solicite educadamente o que falta OU documentos. Seja breve e não exponha regras internas.
                    """
                    st.session_state.ia_inicial = consultar_ia(p_resumo, "Assistente do Frederico")
                    st.session_state.fase = 2; st.rerun()

    if st.session_state.fase == 2:
        st.subheader("2. Confirmação e Complemento")
        st.info(st.session_state.ia_inicial)
        
        opcao = st.radio("Deseja complementar?", ["Apenas seguir para agendamento", "Digitar relato complementar", "Enviar documentos"], horizontal=True)
        
        if opcao == "Digitar relato complementar":
            rel_comp = st.text_area("Complemento:")
            if st.button("Analisar Novo Relato"):
                st.session_state.relato_complementar = rel_comp
                with st.spinner("Reavaliando..."):
                    st.session_state.ia_inicial = consultar_ia(f"Novo relato: {rel_comp}. Se faltar algo, peça, senão confirme.", "Assistente")
                    st.rerun()
                    
        elif opcao == "Enviar documentos":
            # 🆕 MENSAGEM DE SEGURANÇA E LGPD INCLUÍDA AQUI
            st.markdown("""
                <div style="background-color: #f0f2f6; padding: 10px; border-radius: 5px; border-left: 5px solid #007bff;">
                    <strong>🔒 Segurança e Privacidade (LGPD)</strong><br>
                    Os arquivos enviados serão utilizados <strong>apenas para análise inicial</strong> e não serão gravados ou armazenados em nosso servidor permanente.
                </div>
            """, unsafe_allow_html=True)
            arquivo = st.file_uploader("Anexar PDF", type=["pdf"])
            if arquivo:
                st.session_state.nome_arquivo = arquivo.name
                st.session_state.conteudo_arquivo = ler_conteudo_arquivo(arquivo)
                st.success("Documento pronto para análise.")

        col_v, col_r = st.columns(2)
        if col_v.button("✅ Confirmar e Ir para Agenda"): st.session_state.fase = 4; st.rerun()
        if col_r.button("❌ Refazer"): st.session_state.fase = 1; st.rerun()

    if st.session_state.fase == 4:
        st.subheader("🗓️ Agendamento")
        horarios = buscar_horarios_livres(service_calendar)
        horario = st.selectbox("Escolha o Horário:", horarios)
        if st.button("✅ Finalizar Solicitação"):
            with st.spinner("Gerando Dossiê..."):
                d = st.session_state.dados_form
                p_fred = f"""
                Você é o PERITO do Frederico. Analise INTEGRALMENTE:
                Relato: {d['relato']} | Complemento: {st.session_state.relato_complementar} | Conteúdo Doc: {st.session_state.conteudo_arquivo}
                Serviço: {d['servico']} | Salário: {d['salario']}
                Parecer: 1. Grau de dificuldade (1-10). 2. Verbas envolvidas. 3. Valor de mercado estimado. 4. Pontos de risco.
                """
                analise_profunda = consultar_ia(p_fred, "Perito Contábil Trabalhista Sênior")
                
                status = criar_evento_agenda(service_calendar, horario, d['nome'], d['tel'], d['servico'])
                salvar_na_planilha(client_sheets, {
                    "data_hora": datetime.now().strftime("%d/%m %H:%M"), "tipo_usuario": d['tipo'], "nome": d['nome'], "telefone": d['tel'],
                    "melhor_horario": horario, "servico": d['servico'], "ia_inicial": st.session_state.ia_inicial,
                    "complemento_relato": st.session_state.relato_complementar, "nome_arquivo": st.session_state.nome_arquivo,
                    "analise_profunda": analise_profunda, "status_agenda": status
                })
                st.session_state.fase = 5; st.rerun()

    if st.session_state.fase == 5:
        st.balloons(); st.success("✅ Tudo pronto!"); st.button("🔄 Novo", on_click=lambda: st.session_state.clear())

if __name__ == "__main__":
    main()
