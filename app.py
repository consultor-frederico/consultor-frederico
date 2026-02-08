import streamlit as st
import datetime
import json
import requests
import gspread
import re
import PyPDF2
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from datetime import datetime, timedelta

# --- 🚨 CONFIGURAÇÕES 🚨 ---
MINHA_CHAVE = "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"
ID_AGENDA = "a497481e5251098078e6c68882a849680f499f6cef836ab976ffccdaad87689a@group.calendar.google.com"
ID_PASTA_RAIZ = '1ZTZ-6-Q46LOQqLTZsxhdefUgsNypNNMS'
NOME_PLANILHA_GOOGLE = 'Atendimento_Fred'

st.set_page_config(page_title="Consultor Frederico - Cálculos", page_icon="🧮")

FERIADOS_NACIONAIS = ["01/01", "21/04", "01/05", "07/09", "12/10", "02/11", "15/11", "25/12"]
SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive', 'https://www.googleapis.com/auth/calendar']

# --- FUNÇÕES AUXILIARES ---

def ler_conteudo_arquivo(uploaded_file):
    if uploaded_file is None: return ""
    texto_extraido = ""
    try:
        if uploaded_file.type == "application/pdf":
            leitor = PyPDF2.PdfReader(uploaded_file)
            for pagina in leitor.pages:
                texto_extraido += pagina.extract_text() + "\n"
        elif uploaded_file.type == "text/plain":
            texto_extraido = str(uploaded_file.read(), "utf-8")
        return f"\n--- CONTEÚDO DO ANEXO ({uploaded_file.name}) ---\n{texto_extraido}\n"
    except Exception as e: return f"\n[Erro leitura: {e}]\n"

def conectar_google():
    try:
        if "google_credentials" in st.secrets:
            info_chaves = json.loads(st.secrets["google_credentials"]["json_data"])
            creds = Credentials.from_service_account_info(info_chaves, scopes=SCOPES)
        else:
            creds = Credentials.from_service_account_file('credentials.json', scopes=SCOPES)
        return gspread.authorize(creds), build('drive', 'v3', credentials=creds), build('calendar', 'v3', credentials=creds)
    except Exception as e:
        st.error(f"❌ Erro de Conexão: {e}")
        return None, None, None

def consultar_ia(mensagem, sistema, temperatura=0.5):
    try:
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {MINHA_CHAVE}", "Content-Type": "application/json"}
        dados = {"model": "llama-3.3-70b-versatile", "messages": [{"role": "system", "content": sistema}, {"role": "user", "content": mensagem}], "temperature": temperatura}
        resp = requests.post(url, headers=headers, json=dados).json()
        return resp['choices'][0]['message']['content']
    except: return "Sistema indisponível."

def buscar_horarios_livres(service_calendar):
    sugestoes = []
    dia_foco = datetime.now() + timedelta(days=2)
    while len(sugestoes) < 10:
        if dia_foco.weekday() >= 5: 
            dia_foco += timedelta(days=1)
            continue
        comeco = dia_foco.replace(hour=0, minute=0, second=0).isoformat() + 'Z'
        fim = dia_foco.replace(hour=23, minute=59, second=59).isoformat() + 'Z'
        events_result = service_calendar.events().list(calendarId=ID_AGENDA, timeMin=comeco, timeMax=fim, singleEvents=True).execute()
        horas_ocupadas = [int(e['start'].get('dateTime').split('T')[1].split(':')[0]) for e in events_result.get('items', []) if e['start'].get('dateTime')]
        
        dia_txt = f"{dia_foco.strftime('%d/%m')} ({['Seg','Ter','Qua','Qui','Sex'][dia_foco.weekday()]})"
        for h in [9, 10, 11, 13, 14, 15, 16, 17]:
            if h not in horas_ocupadas: sugestoes.append(f"{dia_txt} às {h}:00")
        dia_foco += timedelta(days=1)
    return sugestoes[:10]

def criar_pasta_cliente(service_drive, nome_cliente, nome_servico, arquivo_uploaded):
    try:
        # 1. Cria a pasta na sua cota pessoal (usando ID_PASTA_RAIZ)
        meta = {
            'name': f"{datetime.now().strftime('%Y-%m-%d')} - {nome_cliente} - {nome_servico}", 
            'mimeType': 'application/vnd.google-apps.folder',
            'parents': [ID_PASTA_RAIZ]
        }
        folder = service_drive.files().create(body=meta, fields='id, webViewLink').execute()
        folder_id = folder.get('id')

        # 2. Upload do arquivo (se houver) herdando permissão
        if arquivo_uploaded is not None:
            media = MediaIoBaseUpload(arquivo_uploaded, mimetype=arquivo_uploaded.type, resumable=True)
            file_meta = {'name': arquivo_uploaded.name, 'parents': [folder_id]}
            service_drive.files().create(
                body=file_meta, 
                media_body=media, 
                fields='id',
                supportsAllDrives=True
            ).execute()
        
        service_drive.permissions().create(fileId=folder_id, body={'type': 'anyone', 'role': 'writer'}).execute()
        return folder.get('webViewLink')
    except Exception as e:
        return f"Erro no Drive: {e}"

def criar_evento_agenda(service_calendar, horario_texto, nome, servico):
    try:
        match = re.search(r"(\d{2}/\d{2}).*às (\d{1,2}):(\d{2})", horario_texto)
        if not match: return "Erro Data"
        dia_mes, hora, minuto = match.group(1), int(match.group(2)), int(match.group(3))
        dt_inicio = datetime.strptime(f"{datetime.now().year}/{dia_mes} {hora}:{minuto}", "%Y/%d/%m %H:%M")
        evento = {
            'summary': f'Cálculo: {nome} ({servico})',
            'start': {'dateTime': dt_inicio.isoformat(), 'timeZone': 'America/Sao_Paulo'},
            'end': {'dateTime': (dt_inicio + timedelta(hours=1)).isoformat(), 'timeZone': 'America/Sao_Paulo'}
        }
        service_calendar.events().insert(calendarId=ID_AGENDA, body=evento).execute()
        return "Confirmado"
    except Exception as e: return f"Erro Agenda: {e}"

# --- APLICAÇÃO ---

def main():
    if 'fase' not in st.session_state: st.session_state.fase = 1
    if 'dados_form' not in st.session_state: st.session_state.dados_form = {}
    
    st.title("🧮 Consultor Frederico")
    client_sheets, service_drive, service_calendar = conectar_google()

    # --- FASE 1: COLETA COM MEMÓRIA ---
    if st.session_state.fase == 1:
        d = st.session_state.dados_form
        perfil = st.radio("Perfil:", ["Advogado", "Empresa", "Colaborador"], 
                          index=["Advogado", "Empresa", "Colaborador"].index(d.get("perfil", "Advogado")))
        nome = st.text_input("Nome/Razão Social", value=d.get("nome", ""))
        salario = st.text_input("Salário Base", value=d.get("salario", ""))
        relato = st.text_area("Descreva o caso", value=d.get("relato", ""))

        if st.button("Analisar"):
            st.session_state.dados_form.update({"perfil": perfil, "nome": nome, "salario": salario, "relato": relato})
            st.session_state.fase = 2
            st.rerun()

    # --- FASE 2: CONFIRMAÇÃO ---
    elif st.session_state.fase == 2:
        st.info(f"Analisando caso de: {st.session_state.dados_form['nome']}")
        col1, col2 = st.columns(2)
        if col1.button("✅ Confirmar"): st.session_state.fase = 3; st.rerun()
        if col2.button("❌ Refazer"): st.session_state.fase = 1; st.rerun()

    # --- FASE 3: TRAVA DE ADVOGADO ---
    elif st.session_state.fase == 3:
        if st.session_state.dados_form["perfil"] == "Advogado":
            st.session_state.arquivo = st.file_uploader("Anexar Documento", type=["pdf", "png", "jpg"])
        else:
            st.warning("⚠️ Apenas Advogados podem anexar documentos.")
            st.session_state.arquivo = None
        
        if st.button("Ir para Agendamento"): st.session_state.fase = 4; st.rerun()

    # --- FASE 4: AGENDAMENTO E IA DE MERCADO ---
    elif st.session_state.fase == 4:
        opcoes = buscar_horarios_livres(service_calendar)
        horario = st.selectbox("Escolha um horário", opcoes)
        
        if st.button("Finalizar Agendamento"):
            with st.spinner("IA calculando dificuldade e valores..."):
                # PROMPT COM VALORES DE MERCADO
                guia_precos = "Simples: R$350-600 | Médio: R$800-1800 | Complexo: R$2000+"
                prompt = f"Analise: {st.session_state.dados_form['relato']}. Aponte Dificuldade e Valor Sugerido com base em: {guia_precos}"
                analise_tecnica = consultar_ia(prompt, "Perito Judicial Sênior")
                
                link = criar_pasta_cliente(service_drive, st.session_state.dados_form['nome'], "Calculo", st.session_state.get('arquivo'))
                status = criar_evento_agenda(service_calendar, horario, st.session_state.dados_form['nome'], "Calculo")
                
                st.success(f"Agendado! Link da pasta: {link}")
                st.markdown(f"### 🚩 Análise do Perito:\n{analise_tecnica}")
                if st.button("Novo Atendimento"): st.session_state.clear(); st.rerun()

if __name__ == "__main__":
    main()
