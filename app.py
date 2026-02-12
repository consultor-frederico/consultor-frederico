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
        events_result = service_calendar.events().list(calendarId=ID_AGENDA, timeMin=inicio_iso, timeMax=fim_iso, singleEvents=True, orderBy='startTime').execute()
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
        data_pt, hora_pt = partes[0].split(" ")[0], partes[1]
        data_completa = datetime.strptime(f"{data_pt}/{datetime.now().year} {hora_pt}", "%d/%m/%Y %H:%M")
        evento = {
            'summary': f'Cálculo: {nome} ({servico})',
            'description': f'WhatsApp: {tel}',
            'start': {'dateTime': data_completa.isoformat(), 'timeZone': 'America/Sao_Paulo'},
            'end': {'dateTime': (data_completa + timedelta(hours=1)).isoformat(), 'timeZone': 'America/Sao_Paulo'},
        }
        service_calendar.events().insert(calendarId=ID_AGENDA, body=evento).execute()
        return "Agendado com Sucesso"
    except: return "Erro Agenda"

def salvar_na_planilha(client_sheets, dados):
    try:
        sh = client_sheets.open(NOME_PLANILHA_GOOGLE)
        sheet = sh.sheet1
        if not sheet.get_all_values():
            sheet.append_row(["Data", "Tipo", "Nome/Razão", "Responsável", "Contato", "Email", "CNPJ", "Horário", "Serviço", "Data Prazo", "Relato Inicial", "IA Inicial", "Relato Complementar", "IA Resposta Complementar", "Nome do Arquivo", "IA Análise Profunda", "Status"])
        linha = [
            dados['data_hora'], dados['tipo_usuario'], dados['nome'], dados.get('resp', ''), dados['telefone'], dados['email'], dados.get('cnpj', ''),
            dados['melhor_horario'], dados['servico'], dados.get('prazo', ''), dados['relato_inicial'], dados['ia_inicial'], 
            dados['complemento_relato'], dados['ia_resposta_complementar'], dados['nome_arquivo'], dados['analise_profunda'], dados['status_agenda']
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
    if 'ia_resposta_complementar' not in st.session_state: st.session_state.ia_resposta_complementar = "Sem complemento"
    if 'relato_complementar' not in st.session_state: st.session_state.relato_complementar = "Não enviado"
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
        d = st.session_state.dados_form 
        
        tipo = st.radio("Perfil:", ["Advogado", "Empresa", "Colaborador"], horizontal=True, 
                        index=["Advogado", "Empresa", "Colaborador"].index(d.get("tipo", "Advogado")))
        
        col1, col2 = st.columns(2)
        if tipo == "Empresa":
            nome = col1.text_input("Razão Social", value=d.get("nome", ""))
            cnpj = col2.text_input("CNPJ", key="cnpj_input", on_change=formatar_cnpj_callback, 
                                   placeholder="00.000.000/0000-00", value=d.get("cnpj", ""))
            resp = st.text_input("Nome do Responsável", value=d.get("resp", ""))
            email = st.text_input("E-mail para contato", value=d.get("email", ""))
            tel = st.text_input("WhatsApp (Responsável)", key="tel_input", on_change=formatar_tel_callback, value=d.get("tel", ""))
        else:
            nome = col1.text_input("Nome Completo", value=d.get("nome", ""))
            email = col2.text_input("E-mail", value=d.get("email", ""))
            tel = st.text_input("WhatsApp", key="tel_input", on_change=formatar_tel_callback, value=d.get("tel", ""))
            cnpj = ""
            resp = nome
        
        opcoes_servico = ["Liquidação", "Iniciais", "Impugnação", "Rescisão", "Horas Extras", "Outros"] if tipo == "Advogado" else ["Rescisão", "Horas Extras", "Outros"]
        s_idx = opcoes_servico.index(d.get("servico")) if d.get("servico") in opcoes_servico else 0
        servico = st.selectbox("Serviço:", opcoes_servico, index=s_idx)
        
        c_adm, c_sai = st.columns(2)
        adm = c_adm.text_input("Admissão (DDMMAAAA)", key="adm_input", on_change=formatar_data_adm_callback, value=d.get("adm", ""))
        sai = c_sai.text_input("Saída (DDMMAAAA)", key="sai_input", on_change=formatar_data_sai_callback, value=d.get("sai", ""))
        
        col_sal, col_prazo = st.columns(2)
        salario = col_sal.text_input("Salário Base", key="sal_input", on_change=formatar_salario_callback, value=d.get("salario", ""))
        
        # 🆕 Sugestão 1: Verificação de Prazos (Apenas para Advogado)
        if tipo == "Advogado":
            prazo = col_prazo.text_input("Data Prazo/Citação (DDMMAAAA)", key="prazo_input", on_change=formatar_data_prazo_callback, value=d.get("prazo", ""))
        else:
            prazo = ""
        
        relato = st.text_area("Resumo da Demanda:", value=d.get("relato", ""))

        if st.button("💬 Analisar Solicitação"):
            cnpj_limpo = re.sub(r'\D', '', cnpj)
            if not nome or not tel: 
                st.warning("Preencha o Nome/Razão Social e WhatsApp.")
            elif tipo == "Empresa" and len(cnpj_limpo) != 14:
                st.error("Por favor, informe um CNPJ válido com 14 dígitos.")
            else:
                st.session_state.dados_form.update({"nome": nome, "resp": resp, "tel": tel, "email": email, "cnpj": cnpj, "tipo": tipo, "servico": servico, "adm": adm, "sai": sai, "salario": salario, "relato": relato, "prazo": prazo})
                with st.spinner("Analisando..."):
                    p_resumo = f"""
                    Você é o assistente direto do Consultor Frederico. 
                    Usuário: {nome} | Perfil: {tipo} | Serviço: {servico}
                    Dados preenchidos: Admissão {adm}, Saída {sai}, Salário {salario}, Prazo {prazo}.
                    Relato: '{relato}'

                    REGRAS DE RESPOSTA:
                    1. CUMPRIMENTE O USUÁRIO: Se for Advogado, use 'Dr.' ou 'Dra.' conforme o nome {nome}. Para Empresa ou Colaborador, use 'Sr.' ou 'Sra.' conforme o nome {nome}.
                    2. NÃO descreva seu raciocínio interno. Comece direto na saudação.
                    3. Confirme que entendeu a demanda de forma cordial.
                    4. Se faltar algo essencial, peça educadamente.
                    """
                    st.session_state.ia_inicial = consultar_ia(p_resumo, "Assistente Jurídico Objetivo.")
                    st.session_state.fase = 2; st.rerun()

    if st.session_state.fase == 2:
        st.subheader("2. Confirmação e Complemento")
        st.info(st.session_state.ia_inicial)
        opcao = st.radio("Deseja complementar?", ["Apenas seguir para agendamento", "Digitar relato complementar", "Enviar documentos"], horizontal=True)
        
        if opcao == "Digitar relato complementar":
            rel_comp = st.text_area("Complemento:", value=st.session_state.relato_complementar if st.session_state.relato_complementar != "Não enviado" else "")
            if st.button("Analisar Novo Relato"):
                st.session_state.relato_complementar = rel_comp
                with st.spinner("Reavaliando..."):
                    p_comp = f"O usuário {st.session_state.dados_form['nome']} ({st.session_state.dados_form['tipo']}) complementou: {rel_comp}. Responda se entendeu usando o tratamento Dr/Dra ou Sr/Sra."
                    st.session_state.ia_resposta_complementar = consultar_ia(p_comp, "Assistente Jurídico.")
                    st.rerun()
        elif opcao == "Enviar documentos":
            st.markdown("<div style='background-color: #f0f2f6; padding: 10px;'>🔒 **Privacidade (LGPD):** Arquivos usados apenas para análise técnica e não gravados permanentemente.</div>", unsafe_allow_html=True)
            arquivo = st.file_uploader("Anexar PDF", type=["pdf"])
            if arquivo:
                st.session_state.nome_arquivo = arquivo.name
                st.session_state.conteudo_arquivo = ler_conteudo_arquivo(arquivo)
                st.success(f"Arquivo {arquivo.name} pronto para análise.")

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
                # 🆕 Sugestão 2: Honorários integrados no prompt técnico
                p_fred = f"""
                Você é o PERITO do Frederico. Analise INTEGRALMENTE: 
                Relato 1: {d['relato']} | Relato 2: {st.session_state.relato_complementar} | Conteúdo Doc: {st.session_state.conteudo_arquivo}.
                Serviço: {d['servico']} | Salário: {d['salario']} | Prazo: {d['prazo']}.
                
                Forneça ao Fred um parecer técnico contendo: 
                1. Grau de dificuldade (1-10). 
                2. Verbas envolvidas. 
                3. Estimativa de honorários profissionais sugeridos para este serviço (valor de mercado).
                4. Pontos de risco e urgência (baseado no prazo fornecido).
                """
                analise_profunda = consultar_ia(p_fred, "Perito Contábil Sênior")
                status = criar_evento_agenda(service_calendar, horario, d['nome'], d['tel'], d['servico'])
                salvar_na_planilha(client_sheets, {**d, "data_hora": datetime.now().strftime("%d/%m %H:%M"), "melhor_horario": horario, "relato_inicial": d['relato'], "ia_inicial": st.session_state.ia_inicial, "complemento_relato": st.session_state.relato_complementar, "ia_resposta_complementar": st.session_state.ia_resposta_complementar, "nome_arquivo": st.session_state.nome_arquivo, "analise_profunda": analise_profunda, "status_agenda": status, "tipo_usuario": d['tipo'], "telefone": d['tel']})
                st.session_state.fase = 5; st.rerun()

    if st.session_state.fase == 5:
        st.balloons(); st.success("✅ Tudo pronto!"); st.button("🔄 Novo Atendimento", on_click=lambda: st.session_state.clear())

if __name__ == "__main__":
    main()
