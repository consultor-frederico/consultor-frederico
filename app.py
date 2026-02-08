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
    'https://www.googleapis.com/auth/calendar'
]

NOME_PLANILHA_GOOGLE = 'Atendimento_Fred' 

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

def formatar_moeda(val):
    limpo = re.sub(r'\D', '', str(val))
    if not limpo: return ""
    valor_float = float(limpo) / 100
    return f"R$ {valor_float:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def formatar_data_auto(val):
    limpo = re.sub(r'\D', '', str(val))
    if len(limpo) == 8:
        return f"{limpo[:2]}/{limpo[2:4]}/{limpo[4:]}"
    return val

def formatar_cnpj(val):
    limpo = re.sub(r'\D', '', str(val))
    if len(limpo) == 14:
        return f"{limpo[:2]}.{limpo[2:5]}.{limpo[5:8]}/{limpo[8:12]}-{limpo[12:]}"
    return val

def validar_email(email):
    return "@" in email and "." in email

def validar_data_final(data_str):
    return re.match(r"^\d{2}/\d{2}/\d{4}$", data_str) is not None

def callback_formatar_telefone():
    val = st.session_state.tel_input
    if not val: return
    limpo = re.sub(r'\D', '', str(val))
    if len(limpo) == 11:
        st.session_state.tel_input = f"({limpo[:2]}) {limpo[2:7]}-{limpo[7:]}"
    elif len(limpo) == 10:
        st.session_state.tel_input = f"({limpo[:2]}) {limpo[2:6]}-{limpo[6:]}"

def formatar_telefone(val):
    if not val: return ""
    limpo = re.sub(r'\D', '', str(val))
    if len(limpo) == 11: return f"({limpo[:2]}) {limpo[2:7]}-{limpo[7:]}"
    elif len(limpo) == 10: return f"({limpo[:2]}) {limpo[2:6]}-{limpo[6:]}"
    return val

def formatar_nome_com_titulo(nome, perfil):
    if not nome: return ""
    p_nome = nome.split()[0].title()
    genero_fem = p_nome[-1].lower() == 'a'
    titulo = "Dra." if (perfil == 'Advogado' and genero_fem) else "Dr." if perfil == 'Advogado' else "Sra." if genero_fem else "Sr."
    return f"{titulo} {p_nome}"

def conectar_google():
    try:
        if "google_credentials" in st.secrets:
            info_chaves = json.loads(st.secrets["google_credentials"]["json_data"])
            creds = Credentials.from_service_account_info(info_chaves, scopes=SCOPES)
        else:
            creds = Credentials.from_service_account_file('credentials.json', scopes=SCOPES)
        return gspread.authorize(creds), build('calendar', 'v3', credentials=creds)
    except Exception as e:
        st.error(f"❌ Erro de Conexão: {e}")
        return None, None

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
        if dia_foco.weekday() >= 5 or dia_foco.strftime("%d/%m") in FERIADOS_NACIONAIS:
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

def criar_evento_agenda(service_calendar, horario_texto, nome, tel, servico):
    try:
        match = re.search(r"(\d{2}/\d{2}).*às (\d{1,2}):(\d{2})", horario_texto)
        if not match: return "Erro Data"
        dia_mes, hora, minuto = match.group(1), int(match.group(2)), int(match.group(3))
        dt_inicio = datetime.strptime(f"{datetime.now().year}/{dia_mes} {hora}:{minuto}", "%Y/%d/%m %H:%M")
        evento = {
            'summary': f'Cálculo: {nome} ({servico})',
            'description': f'Tel: {tel}\nSolicitação Web App.',
            'start': {'dateTime': dt_inicio.isoformat(), 'timeZone': 'America/Sao_Paulo'},
            'end': {'dateTime': (dt_inicio + timedelta(hours=1)).isoformat(), 'timeZone': 'America/Sao_Paulo'}
        }
        service_calendar.events().insert(calendarId=ID_AGENDA, body=evento).execute()
        return "Confirmado"
    except Exception as e: return f"Erro Agenda: {e}"

def salvar_na_planilha(client_sheets, dados):
    try:
        sheet = client_sheets.open(NOME_PLANILHA_GOOGLE).sheet1
        if not sheet.get_all_values(): 
            sheet.append_row(["Data", "Tipo", "Nome", "Contato", "Email", "Horário", "Serviço", "Resumo Cliente", "Análise Técnica", "Status Arquivo", "Status"])
        sheet.append_row([
            dados['data_hora'], dados['tipo_usuario'], dados['nome'], dados['telefone'], dados['email'],
            dados['melhor_horario'], dados['servico'], dados['analise_cliente'], dados['analise_tecnica'],
            "Processado em Memória (Não Salvo)", dados['status_agenda']
        ])
    except: pass

# --- APLICAÇÃO PRINCIPAL ---
def main():
    if 'fase' not in st.session_state: st.session_state.fase = 1
    if 'dados_form' not in st.session_state: st.session_state.dados_form = {}
    if 'conteudo_arquivo' not in st.session_state: st.session_state.conteudo_arquivo = ""

    client_sheets, service_calendar = conectar_google()

    if st.session_state.fase == 1:
        st.subheader("1. Identificação e Caso")
        d = st.session_state.dados_form
        perfil_list = ["Advogado", "Empresa", "Colaborador"]
        perfil_idx = perfil_list.index(d.get("tipo", "Advogado"))
        tipo = st.radio("Perfil:", perfil_list, horizontal=True, index=perfil_idx)
        
        col1, col2 = st.columns(2)
        if tipo == "Empresa":
            nome = col1.text_input("Razão Social", value=d.get("nome", ""))
            raw_cnpj = col2.text_input("CNPJ (apenas números)", value=d.get("cnpj", ""), placeholder="00000000000100")
            cnpj = formatar_cnpj(raw_cnpj)
            if cnpj: st.caption(f"CNPJ: **{cnpj}**")
            n_resp = st.text_input("Nome do Responsável", value=d.get("nome_resp", ""))
        else:
            nome = col1.text_input("Nome Completo", value=d.get("nome", ""))
            n_resp = nome
            cnpj = ""
            
        c_tel, c_mail = st.columns(2)
        tel = c_tel.text_input("WhatsApp", value=d.get("tel", ""), key="tel_input", on_change=callback_formatar_telefone)
        mail = c_mail.text_input("E-mail", value=d.get("email", ""))
        
        opcoes_servico = ["Liquidação de Sentença", "Inicial/Estimativa", "Impugnação", "Rescisão", "Horas Extras", "Outros"] if tipo == "Advogado" else ["Rescisão", "Horas Extras", "Outros"]
        try: serv_idx = opcoes_servico.index(d.get("servico", ""))
        except: serv_idx = 0
        servico = st.selectbox("Tipo de Cálculo:", opcoes_servico, index=serv_idx)
        
        c_adm, c_sai = st.columns(2)
        raw_adm = c_adm.text_input("Admissão (apenas números)", value=d.get("adm", ""), placeholder="Ex: 01052020")
        raw_sai = c_sai.text_input("Saída (apenas números)", value=d.get("sai", ""), placeholder="Ex: 10022026")
        
        adm = formatar_data_auto(raw_adm)
        sai = formatar_data_auto(raw_sai)
        
        raw_salario = st.text_input("Salário Base (apenas números)", value=d.get("salario", ""), placeholder="Ex: 250000")
        salario = formatar_moeda(raw_salario)
        if salario: st.caption(f"Salário: **{salario}**")
        
        relato = st.text_area("Resumo da Demanda:", value=d.get("relato", ""), height=100)

        if st.button("💬 Analisar Solicitação"):
            if not nome or not tel: 
                st.warning("Preencha Nome e Telefone.")
            elif tipo == "Empresa" and len(re.sub(r'\D', '', raw_cnpj)) != 14:
                st.error("CNPJ deve conter 14 números.")
            elif mail and not validar_email(mail):
                st.error("E-mail inválido!")
            elif not validar_data_final(adm) or not validar_data_final(sai):
                st.error("Verifique as datas (DDMMYYYY)")
            else:
                n_tratado = formatar_nome_com_titulo(n_resp, tipo)
                st.session_state.dados_form.update({
                    "nome": nome, "nome_resp": n_resp, "tel": tel, "email": mail, "cnpj": cnpj,
                    "tipo": tipo, "servico": servico, "relato": relato, "salario": salario,
                    "adm": adm, "sai": sai,
                    "tecnico": f"Tipo: {servico}. Salário: {salario}. Período: {adm} a {sai}."
                })
                p_c = f"Aja como o Frederico. Entenda o relato: '{relato}'. Resuma em 1 parágrafo curto."
                st.session_state.ia_resumo_cliente = consultar_ia(p_c, "Consultor Jurídico")
                st.session_state.fase = 2
                st.rerun()

    if st.session_state.fase == 2:
        st.subheader("2. Confirmação")
        st.info(st.session_state.ia_resumo_cliente)
        col_s, col_n = st.columns(2)
        if col_n.button("❌ Não (Refazer)"): st.session_state.fase = 1; st.rerun()
        if col_s.button("✅ Sim, está correto"): st.session_state.fase = 3; st.rerun()

    if st.session_state.fase == 3:
        st.subheader("3. Complemento e Documentos")
        st.warning("🔒 Análise temporária. Seus arquivos não serão salvos.")
        comp = st.text_input("Observação Adicional (Opcional):")
        arquivo_uploaded = st.file_uploader("Anexar Documentos para a IA ler", type=["pdf", "txt", "jpg", "png"])
        if arquivo_uploaded:
            st.session_state.conteudo_arquivo = ler_conteudo_arquivo(arquivo_uploaded)
        
        if st.button("🔽 Seguir para Agendamento"):
            if comp: st.session_state.dados_form["relato"] += f" [Extra: {comp}]"
            st.session_state.fase = 4
            st.rerun()

    if st.session_state.fase == 4:
        st.subheader("🗓️ Finalizar Agendamento")
        opcoes = buscar_horarios_livres(service_calendar)
        horario = st.selectbox("Escolha o Horário:", opcoes)
        if st.button("✅ Confirmar Agendamento"):
            with st.spinner("IA analisando tudo..."):
                d = st.session_state.dados_form
                tel_f = formatar_telefone(d['tel'])
                
                p_t = f"""
                AJA COMO O PERITO FREDERICO. contexto {d['tipo']}.
                DADOS: {d['tecnico']}. RELATO: {d['relato']}. ANEXO: {st.session_state.conteudo_arquivo}.
                Determine Dificuldade e Valor Sugerido (Mercado 2026).
                Extraia o máximo de informações do anexo (processos, partes, valores).
                """
                
                analise_ia = consultar_ia(p_t, "Perito Judicial Sênior", 0.2)
                status = criar_evento_agenda(service_calendar, horario, d['nome'], tel_f, d['servico'])
                
                salvar_na_planilha(client_sheets, {
                    "data_hora": datetime.now().strftime("%d/%m %H:%M"),
                    "tipo_usuario": d['tipo'], "nome": d['nome'], "telefone": tel_f, "email": d['email'],
                    "melhor_horario": horario, "servico": d['servico'],
                    "analise_cliente": st.session_state.ia_resumo_cliente,
                    "analise_tecnica": analise_ia,
                    "status_agenda": status
                })
                
                st.success(f"✅ Agendado para {horario}!")
                st.markdown(f"### Análise do Perito:\n{analise_ia}")
                st.session_state.fase = 5
                st.rerun()

    if st.session_state.fase == 5:
        st.balloons()
        if st.button("🔄 Novo Atendimento"): st.session_state.clear(); st.rerun()
        if st.button("🏁 Sair"): st.session_state.encerrado = True; st.rerun()

if __name__ == "__main__":
    main()
