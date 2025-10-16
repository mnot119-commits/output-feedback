import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import googleapiclient.discovery
import google.generativeai as genai
import pandas as pd
import re
import json
from datetime import datetime

# ----------------------------------------------------------------------
# 초기 설정 및 페이지 구성
# ----------------------------------------------------------------------
st.set_page_config(
    page_title="AI 기반 학생 피드백 시스템",
    page_icon="🤖",
    layout="wide",
)

# ----------------------------------------------------------------------
# 구글 API 및 Gemini API 설정 함수
# Streamlit Secrets를 사용하여 민감한 정보 관리
# ----------------------------------------------------------------------

def setup_connections():
    """Google Sheets, Google Docs, Gemini API에 연결합니다."""
    try:
        # Google Service Account Credentials 설정
        creds_json = {
            "type": st.secrets["gcp_service_account"]["type"],
            "project_id": st.secrets["gcp_service_account"]["project_id"],
            "private_key_id": st.secrets["gcp_service_account"]["private_key_id"],
            "private_key": st.secrets["gcp_service_account"]["private_key"].replace('\\n', '\n'),
            "client_email": st.secrets["gcp_service_account"]["client_email"],
            "client_id": st.secrets["gcp_service_account"]["client_id"],
            "auth_uri": st.secrets["gcp_service_account"]["auth_uri"],
            "token_uri": st.secrets["gcp_service_account"]["token_uri"],
            "auth_provider_x509_cert_url": st.secrets["gcp_service_account"]["auth_provider_x509_cert_url"],
            "client_x509_cert_url": st.secrets["gcp_service_account"]["client_x509_cert_url"]
        }
        
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
            "https://www.googleapis.com/auth/documents.readonly"
        ]
        creds = Credentials.from_service_account_info(creds_json, scopes=scopes)
        
        # Google Sheets 연결
        gs = gspread.authorize(creds)
        
        # Google Docs 연결
        docs_service = googleapiclient.discovery.build('docs', 'v1', credentials=creds)

        # Gemini API 설정
        genai.configure(api_key=st.secrets["gemini_api_key"]["api_key"])
        model = genai.GenerativeModel('gemini-1.5-flash')

        return gs, docs_service, model
    except Exception as e:
        st.error(f"API 연결 중 오류가 발생했습니다: {e}")
        st.info("Streamlit Secrets 설정을 확인해주세요. (gcp_service_account, gemini_api_key)")
        return None, None, None

# ----------------------------------------------------------------------
# 데이터베이스 (Google Sheets) 관련 함수
# ----------------------------------------------------------------------

def get_sheet(gs_client, sheet_name):
    """지정된 이름의 구글 시트를 가져옵니다. 없으면 생성합니다."""
    try:
        spreadsheet = gs_client.open_by_key(st.secrets["google_sheet_key"]["sheet_key"])
    except gspread.exceptions.SpreadsheetNotFound:
        st.error("지정된 Key의 구글 스프레드시트를 찾을 수 없습니다.")
        st.info("`secrets.toml` 파일에 올바른 `sheet_key`를 입력했는지 확인해주세요.")
        return None
        
    try:
        worksheet = spreadsheet.worksheet(sheet_name)
    except gspread.exceptions.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title=sheet_name, rows="100", cols="20")
        if sheet_name == "users":
            worksheet.append_row(["student_id", "password"])
            worksheet.append_row(["240000", "1234"]) # 예시 학생 데이터
        elif sheet_name == "submissions":
             worksheet.append_row(["student_id", "class_name", "timestamp", "submission_content", "feedback", "record_suggestion"])
    return worksheet

# ----------------------------------------------------------------------
# 인증 및 로그인 관련 함수
# ----------------------------------------------------------------------

def login(users_sheet):
    """로그인 UI를 표시하고 인증을 처리합니다."""
    st.header("🤖 AI 기반 학생 피드백 시스템")
    st.markdown("---")

    if 'logged_in' not in st.session_state:
        st.session_state['logged_in'] = False

    if not st.session_state['logged_in']:
        with st.form("login_form"):
            student_id = st.text_input("학번")
            password = st.text_input("비밀번호", type="password")
            submitted = st.form_submit_button("로그인")
            
            if submitted:
                if not student_id or not password:
                    st.warning("학번과 비밀번호를 모두 입력해주세요.")
                    return
                
                users_df = pd.DataFrame(users_sheet.get_all_records())
                user = users_df[(users_df['student_id'].astype(str) == student_id) & (users_df['password'].astype(str) == password)]

                if not user.empty:
                    st.session_state['logged_in'] = True
                    st.session_state['student_id'] = student_id
                    st.rerun()
                else:
                    st.error("학번 또는 비밀번호가 올바르지 않습니다.")

def logout():
    """로그아웃 처리."""
    if st.sidebar.button("로그아웃"):
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.rerun()

# ----------------------------------------------------------------------
# Google Docs 템플릿 처리 함수
# ----------------------------------------------------------------------
def get_doc_content(docs_service, document_id):
    """Google Docs 문서 내용을 텍스트로 가져옵니다."""
    try:
        document = docs_service.documents().get(documentId=document_id).execute()
        content = document.get('body').get('content')
        text = ''
        for value in content:
            if 'paragraph' in value:
                elements = value.get('paragraph').get('elements')
                for elem in elements:
                    text += elem.get('textRun', {}).get('content', '')
        return text
    except Exception as e:
        st.error(f"Google Docs 문서를 불러오는 중 오류 발생: {e}")
        st.warning(f"문서 ID '{document_id}'가 올바른지, 서비스 계정에 문서 읽기 권한이 있는지 확인하세요.")
        return None

def parse_template(template_text):
    """템플릿 텍스트에서 입력 필드를 파싱합니다."""
    # 정규표현식: {{label:placeholder}} 형식의 패턴 찾기
    pattern = re.compile(r'\{\{([^:]+):([^}]+)\}\}')
    
    parts = []
    last_end = 0
    for match in pattern.finditer(template_text):
        start, end = match.span()
        # 매칭된 부분 이전의 텍스트 추가
        parts.append({'type': 'static', 'content': template_text[last_end:start]})
        
        # 매칭된 부분(입력 필드) 정보 추가
        label = match.group(1).strip()
        placeholder = match.group(2).strip()
        parts.append({'type': 'input', 'label': label, 'placeholder': placeholder})
        
        last_end = end
    
    # 마지막 매칭 이후의 텍스트 추가
    parts.append({'type': 'static', 'content': template_text[last_end:]})
    
    return parts

# ----------------------------------------------------------------------
# 데이터 로드 및 저장 함수
# ----------------------------------------------------------------------
def load_previous_submission(submissions_sheet, student_id, class_name):
    """이전 제출 내용을 불러옵니다."""
    submissions_df = pd.DataFrame(submissions_sheet.get_all_records())
    if submissions_df.empty:
        return None, "", ""
        
    student_submissions = submissions_df[
        (submissions_df['student_id'].astype(str) == str(student_id)) &
        (submissions_df['class_name'] == class_name)
    ]

    if not student_submissions.empty:
        latest_submission = student_submissions.sort_values(by='timestamp', ascending=False).iloc[0]
        return json.loads(latest_submission['submission_content']), latest_submission['feedback'], latest_submission['record_suggestion']
    return None, "", ""

def save_submission(submissions_sheet, student_id, class_name, submission_content, feedback, record_suggestion):
    """제출 내용을 구글 시트에 저장하거나 업데이트합니다."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    submission_json = json.dumps(submission_content, ensure_ascii=False)
    
    df = pd.DataFrame(submissions_sheet.get_all_records())
    
    # 기존 제출 기록 찾기
    existing_row = df[(df['student_id'].astype(str) == str(student_id)) & (df['class_name'] == class_name)]

    if not existing_row.empty:
        # gspread는 행 번호가 1부터 시작, 헤더 포함이므로 +2
        row_index = existing_row.index[0] + 2
        submissions_sheet.update_cell(row_index, 3, timestamp)
        submissions_sheet.update_cell(row_index, 4, submission_json)
        submissions_sheet.update_cell(row_index, 5, feedback)
        submissions_sheet.update_cell(row_index, 6, record_suggestion)
    else:
        # 새 기록 추가
        new_row = [student_id, class_name, timestamp, submission_json, feedback, record_suggestion]
        submissions_sheet.append_row(new_row)

# ----------------------------------------------------------------------
# Gemini API 호출 함수
# ----------------------------------------------------------------------
def get_ai_feedback(model, class_name, submission_content):
    """Gemini API를 호출하여 피드백과 생기부 초안을 생성합니다."""
    # 제출 내용을 하나의 문자열로 결합
    full_text = f"## 수업: {class_name}\n\n"
    for label, content in submission_content.items():
        full_text += f"### {label}\n{content}\n\n"

    # 1. 학생을 위한 피드백 생성 프롬프트
    feedback_prompt = f"""
        당신은 고등학생의 학습 활동을 지도하는 친절하고 유능한 교사입니다.
        아래 학생이 제출한 내용을 바탕으로, 학생의 성장을 돕는 건설적인 피드백을 작성해주세요.

        [피드백 작성 가이드라인]
        1. 칭찬할 점: 학생의 아이디어나 노력에서 긍정적인 부분을 구체적으로 언급하여 동기를 부여해주세요.
        2. 개선할 점: 내용의 논리, 깊이, 창의성 측면에서 보완할 부분을 구체적인 예시와 함께 제안해주세요.
        3. 심화 탐구 제안: 학생의 생각을 더 발전시킬 수 있는 질문이나 관련 자료, 활동을 추천해주세요.
        4. 어조: 학생이 상처받지 않도록, 긍정적이고 격려하는 어조를 사용해주세요.
        5. 형식: 각 항목을 명확하게 구분하여 번호를 붙여 설명해주세요.

        [학생 제출 내용]
        {full_text}

        자, 이제 위의 가이드라인에 따라 학생을 위한 피드백을 작성해주세요.
    """

    # 2. 생활기록부 '과목별 세부능력 및 특기사항' 초안 생성 프롬프트
    record_prompt = f"""
        당신은 학생의 활동을 관찰하고 핵심 역량을 파악하여 학교생활기록부에 기록하는 대한민국 고등학교 교사입니다.
        아래 학생의 제출물을 바탕으로, '과목별 세부능력 및 특기사항'에 기재할 수 있는 구체적이고 객관적인 서술형 초안을 작성해주세요.

        [초안 작성 가이드라인]
        1. 핵심 역량 추출: 학생의 글에서 드러나는 비판적 사고력, 창의적 문제 해결 능력, 정보 활용 능력, 의사소통 능력 등 핵심 역량을 구체적인 근거와 함께 서술해주세요. (예: '~~라는 자료를 분석하여 ~~라는 독창적인 대안을 제시하는 등 창의적 문제 해결 능력이 돋보임.')
        2. 과정 중심 서술: 학생이 어떤 고민을 했고, 어떤 과정을 통해 결과물을 만들었는지가 드러나도록 서술해주세요.
        3. 객관적 서술: '매우 뛰어남', '훌륭함'과 같은 주관적인 표현 대신, 학생의 활동과 그 결과를 바탕으로 객관적으로 서술해주세요.
        4. 분량: 1~2개의 문장으로 간결하게 요약해주세요.
        5. 문체: '~함.', '~음.'으로 끝나는 개조식 문체를 사용해주세요.

        [학생 제출 내용]
        {full_text}

        이제 위의 가이드라인에 따라 생기부 초안을 작성해주세요.
    """

    try:
        with st.spinner("AI가 피드백을 생성하고 있습니다... 잠시만 기다려주세요."):
            feedback_response = model.generate_content(feedback_prompt)
            record_response = model.generate_content(record_prompt)
        
        return feedback_response.text, record_response.text
    except Exception as e:
        st.error(f"Gemini API 호출 중 오류가 발생했습니다: {e}")
        return "피드백 생성에 실패했습니다.", "생기부 초안 생성에 실패했습니다."

# ----------------------------------------------------------------------
# 메인 애플리케이션 로직
# ----------------------------------------------------------------------
def main():
    gs, docs_service, model = setup_connections()
    if not all([gs, docs_service, model]):
        st.stop()

    users_sheet = get_sheet(gs, "users")
    submissions_sheet = get_sheet(gs, "submissions")
    if not users_sheet or not submissions_sheet:
        st.warning("Google Sheets에 연결할 수 없습니다. 설정을 확인해주세요.")
        st.stop()

    if 'logged_in' not in st.session_state or not st.session_state['logged_in']:
        login(users_sheet)
    else:
        st.sidebar.success(f"{st.session_state['student_id']}님, 환영합니다.")
        logout()
        st.sidebar.markdown("---")
        
        # 수업 목록 설정 (key: 수업 이름, value: Google Docs ID)
        # 이 부분에 선생님의 수업과 구글 문서 ID를 추가하시면 됩니다.
        CLASS_LIST = {
            "주제 탐구 보고서 작성법": "1SOq_wJjl_7q47uALaN7PV26aF_3s-S_z_WkL_o_U-Yw",
            "인공지능 윤리 토론 개요서": "15k_sXbapCElqQmBQuOBm-e9H3v_s0q_Z_cO-dIeF_gA"
        }
        
        # 예시 문서 안내
        st.sidebar.info("""
        **수업 추가 안내**
        
        위 수업 목록은 예시입니다. 
        `app.py` 코드의 `CLASS_LIST` 딕셔너리에
        `"수업이름": "구글문서ID"` 형식으로
        새로운 수업을 추가할 수 있습니다.
        """)

        class_name = st.sidebar.radio("수업 선택", list(CLASS_LIST.keys()))
        
        st.header(f"📝 {class_name}")
        st.markdown("---")

        doc_id = CLASS_LIST[class_name]
        template_text = get_doc_content(docs_service, doc_id)

        if template_text:
            parsed_template = parse_template(template_text)
            
            # 이전 제출 내용 불러오기
            prev_submission, prev_feedback, prev_record = load_previous_submission(
                submissions_sheet, st.session_state['student_id'], class_name
            )

            submission_content = {}
            with st.form("submission_form"):
                for part in parsed_template:
                    if part['type'] == 'static':
                        st.markdown(part['content'], unsafe_allow_html=True)
                    elif part['type'] == 'input':
                        # 이전 제출 내용이 있으면 채워넣기
                        prev_value = prev_submission.get(part['label'], "") if prev_submission else ""
                        submission_content[part['label']] = st.text_area(
                            label=part['label'],
                            value=prev_value,
                            placeholder=part['placeholder'],
                            height=150
                        )
                
                submit_button = st.form_submit_button("제출 및 AI 피드백 받기", type="primary")

            if submit_button:
                # 모든 필드가 채워졌는지 확인
                if all(value.strip() for value in submission_content.values()):
                    feedback, record_suggestion = get_ai_feedback(model, class_name, submission_content)
                    
                    save_submission(submissions_sheet, st.session_state['student_id'], class_name, submission_content, feedback, record_suggestion)
                    
                    # 피드백과 생기부 기록을 세션 상태에 저장하여 다시 표시
                    st.session_state[f'{class_name}_feedback'] = feedback
                    st.session_state[f'{class_name}_record'] = record_suggestion
                    st.rerun()

                else:
                    st.warning("모든 항목을 작성해주세요.")

            # 세션 상태에 저장된 피드백/생기부 기록이 있으면 표시
            if f'{class_name}_feedback' in st.session_state:
                st.markdown("---")
                st.subheader("🤖 AI 피드백")
                st.markdown(st.session_state[f'{class_name}_feedback'])

                st.subheader("📚 생활기록부 '세부능력 및 특기사항' 기록 예시")
                st.info("이 내용은 선생님의 기록을 돕기 위한 참고 자료입니다.")
                st.markdown(st.session_state[f'{class_name}_record'])
            # 이전 제출 기록이 있고, 새로고침 된 경우 (세션 상태에 없는 경우)
            elif prev_feedback:
                st.markdown("---")
                st.subheader("🤖 AI 피드백 (이전 기록)")
                st.markdown(prev_feedback)

                st.subheader("📚 생활기록부 '세부능력 및 특기사항' 기록 예시 (이전 기록)")
                st.info("이 내용은 선생님의 기록을 돕기 위한 참고 자료입니다.")
                st.markdown(prev_record)


if __name__ == "__main__":
    main()
