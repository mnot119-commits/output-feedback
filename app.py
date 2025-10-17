import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import googleapiclient.discovery
import google.generativeai as genai
import pandas as pd
import re
import json
from datetime import datetime
from collections import OrderedDict

# ----------------------------------------------------------------------
# 초기 설정 및 페이지 구성
# ----------------------------------------------------------------------
st.set_page_config(
    page_title="AI 기반 학생 피드백 시스템",
    page_icon="🤖",
    layout="wide",
)

# ----------------------------------------------------------------------
# API 연결 및 인증
# ----------------------------------------------------------------------
def setup_connections():
    """Google Sheets, Docs, Gemini API에 연결합니다."""
    try:
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
        gs = gspread.authorize(creds)
        docs_service = googleapiclient.discovery.build('docs', 'v1', credentials=creds)
        genai.configure(api_key=st.secrets["gemini_api_key"]["api_key"])
        # [수정] 안정적인 모델 이름과 JSON 출력을 위한 설정 추가
        generation_config = {"response_mime_type": "application/json"}
        model = genai.GenerativeModel('gemini-1.0-pro', generation_config=generation_config)
        return gs, docs_service, model
    except Exception as e:
        st.error(f"API 연결 중 오류가 발생했습니다: {e}")
        st.info("Streamlit Secrets 설정을 확인해주세요.")
        return None, None, None

def get_sheet(gs_client, sheet_name):
    """지정된 이름의 구글 시트를 가져오고, 필요한 열이 없으면 추가합니다."""
    try:
        spreadsheet = gs_client.open_by_key(st.secrets["google_sheet_key"]["sheet_key"])
    except gspread.exceptions.SpreadsheetNotFound:
        st.error("지정된 Key의 구글 스프레드시트를 찾을 수 없습니다.")
        return None
    try:
        worksheet = spreadsheet.worksheet(sheet_name)
    except gspread.exceptions.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title=sheet_name, rows="100", cols="20")
        if sheet_name == "users":
            worksheet.append_row(["student_id", "password", "password_changed"])
        elif sheet_name == "submissions":
             worksheet.append_row(["student_id", "class_name", "timestamp", "submission_content", "feedback", "record_suggestion"])
    
    if sheet_name == "users":
        headers = worksheet.row_values(1)
        if "password_changed" not in headers:
            worksheet.update_cell(1, len(headers) + 1, "password_changed")

    return worksheet

def login(users_sheet):
    """로그인 UI를 표시하고 학생/교사 인증을 처리합니다."""
    st.header("🤖 AI 기반 학생 피드백 시스템")
    st.markdown("---")
    if 'logged_in' not in st.session_state:
        st.session_state['logged_in'] = False
    if not st.session_state['logged_in']:
        with st.form("login_form"):
            user_id = st.text_input("아이디 (학번 또는 교사 ID)")
            password = st.text_input("비밀번호", type="password")
            submitted = st.form_submit_button("로그인")

            if submitted:
                teacher_creds = st.secrets.get("teacher_account", {})
                if user_id == teacher_creds.get("id") and password == teacher_creds.get("password"):
                    st.session_state['logged_in'] = True
                    st.session_state['user_id'] = user_id
                    st.session_state['is_teacher'] = True
                    st.rerun()
                else:
                    users_df = pd.DataFrame(users_sheet.get_all_records())
                    if users_df.empty:
                        st.error("등록된 학생 정보가 없습니다.")
                        return

                    user_row = users_df[(users_df['student_id'].astype(str) == user_id) & (users_df['password'].astype(str) == password)]
                    
                    if not user_row.empty:
                        st.session_state['logged_in'] = True
                        st.session_state['user_id'] = user_id
                        st.session_state['is_teacher'] = False
                        
                        password_changed_val = user_row.get('password_changed', pd.Series(False)).iloc[0]
                        if str(password_changed_val).upper() != 'TRUE':
                            st.session_state['password_needs_change'] = True
                        else:
                            st.session_state['password_needs_change'] = False
                        st.rerun()
                    else:
                        st.error("아이디 또는 비밀번호가 올바르지 않습니다.")

def logout():
    """로그아웃 처리."""
    if st.sidebar.button("로그아웃"):
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.rerun()

def change_password_view(users_sheet):
    """학생이 첫 로그인 시 비밀번호를 변경하도록 하는 UI를 표시합니다."""
    st.header("🔒 비밀번호 변경")
    st.info("시스템에 처음 로그인하셨습니다. 보안을 위해 비밀번호를 변경해주세요.")
    
    with st.form("change_password_form"):
        new_password = st.text_input("새 비밀번호", type="password")
        confirm_password = st.text_input("새 비밀번호 확인", type="password")
        submitted = st.form_submit_button("비밀번호 변경")

        if submitted:
            if not new_password or not confirm_password:
                st.warning("새 비밀번호와 확인을 모두 입력해주세요.")
            elif new_password != confirm_password:
                st.error("입력한 두 비밀번호가 일치하지 않습니다.")
            else:
                try:
                    student_id = st.session_state['user_id']
                    cell = users_sheet.find(student_id)
                    users_sheet.update_cell(cell.row, 2, new_password)
                    users_sheet.update_cell(cell.row, 3, 'TRUE')
                    st.session_state['password_needs_change'] = False
                    st.success("비밀번호가 성공적으로 변경되었습니다. 이제 앱을 사용하실 수 있습니다.")
                    st.balloons()
                    st.rerun()
                except Exception as e:
                    st.error(f"비밀번호 변경 중 오류가 발생했습니다: {e}")

# ----------------------------------------------------------------------
# 템플릿 처리 및 AI 피드백 함수
# ----------------------------------------------------------------------
def get_doc_content(docs_service, document_id):
    try:
        document = docs_service.documents().get(documentId=document_id).execute()
        content = document.get('body').get('content')
        text = ''
        for value in content:
            if 'paragraph' in value:
                elements = value.get('paragraph').get('elements')
                for elem in elements:
                    text_run = elem.get('textRun')
                    if text_run:
                        text += text_run.get('content', '')
                text += '\n'
        return text
    except Exception as e:
        return None

def parse_template_by_activity(template_text):
    activities = OrderedDict()
    input_pattern = re.compile(r'\{\{([^:}]+)(?::([^}]+))?\}\}')
    exemplar_pattern = re.compile(r'<<<exemplar\s*\n(.*?)\n\s*>>>', re.DOTALL)
    parts = re.split(r'\n## ', '\n' + template_text)
    for part in parts[1:]:
        lines = part.split('\n')
        activity_title = lines[0].strip()
        content_text = '\n'.join(lines[1:])
        exemplar_match = exemplar_pattern.search(content_text)
        exemplar_text = ""
        if exemplar_match:
            exemplar_text = exemplar_match.group(1).strip()
            content_text = exemplar_pattern.sub('', content_text)
        activity_parts = []
        last_end = 0
        for match in input_pattern.finditer(content_text):
            start, end = match.span()
            activity_parts.append({'type': 'static', 'content': content_text[last_end:start]})
            label = match.group(1).strip()
            placeholder = match.group(2).strip() if match.group(2) else "여기에 내용을 입력하세요."
            activity_parts.append({'type': 'input', 'label': label, 'placeholder': placeholder})
            last_end = end
        activity_parts.append({'type': 'static', 'content': content_text[last_end:]})
        activities[activity_title] = {'parts': activity_parts, 'exemplar': exemplar_text}
    return activities

def load_previous_submission(submissions_sheet, student_id, class_name):
    try:
        submissions_df = pd.DataFrame(submissions_sheet.get_all_records())
        if submissions_df.empty: return {}, ""
        student_submissions = submissions_df[(submissions_df['student_id'].astype(str) == str(student_id)) & (submissions_df['class_name'] == class_name)]
        if not student_submissions.empty:
            latest_submission = student_submissions.sort_values(by='timestamp', ascending=False).iloc[0]
            content = latest_submission['submission_content']
            if content and content.strip(): return json.loads(content), latest_submission['feedback']
            else: return {}, latest_submission['feedback']
    except Exception: pass
    return {}, ""

def save_submission(submissions_sheet, student_id, class_name, submission_content, feedback, record_suggestion):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    submission_json = json.dumps(submission_content, ensure_ascii=False)
    df = pd.DataFrame(submissions_sheet.get_all_records())
    if df.empty or 'student_id' not in df.columns: existing_row = pd.DataFrame()
    else: existing_row = df[(df['student_id'].astype(str) == str(student_id)) & (df['class_name'] == class_name)]
    if not existing_row.empty:
        row_index = existing_row.index[0] + 2
        submissions_sheet.update_cell(row_index, 3, timestamp)
        submissions_sheet.update_cell(row_index, 4, submission_json)
        submissions_sheet.update_cell(row_index, 5, feedback)
        submissions_sheet.update_cell(row_index, 6, record_suggestion)
    else:
        new_row = [student_id, class_name, timestamp, submission_json, feedback, record_suggestion]
        submissions_sheet.append_row(new_row)

# [수정] API 요청을 하나로 통합한 함수
def get_ai_feedback(model, class_name, submission_content, all_exemplars_text):
    """하나의 API 호출로 피드백과 생기부 초안을 모두 생성합니다."""
    full_text = f"## 수업: {class_name}\n\n"
    submitted_items = {k: v for k, v in submission_content.items() if v and v.strip()}
    if not submitted_items:
        return "제출된 내용이 없어 피드백을 생성할 수 없습니다.", "제출된 내용이 없어 생기부 초안을 생성할 수 없습니다."

    for label, content in submitted_items.items():
        full_text += f"### {label}\n{content}\n\n"
    
    context_prompt = ""
    if all_exemplars_text and all_exemplars_text.strip():
        context_prompt = f"[교사 제공 참고자료 (모범답안/평가 기준)]\n{all_exemplars_text}\n\n"
    
    prompt = f"""
당신은 대한민국 고등학교 교사로서, 학생의 제출물을 분석하고 두 가지 결과물을 JSON 형식으로 출력해야 합니다.

{context_prompt}

[학생 제출 내용]
{full_text}

[요청 사항]
아래 두 항목에 대한 내용을 각각 작성하여, 반드시 다음 JSON 형식에 맞춰 한 번에 출력해주세요.

{{
  "feedback": "여기에 학생을 위한 건설적인 피드백을 작성합니다. (칭찬, 개선점, 심화 탐구 제안 포함, 격려하는 어조 사용)",
  "record_suggestion": "여기에 '과목별 세부능력 및 특기사항'에 기재할 객관적인 서술형 초안을 작성합니다. (핵심 역량, 과정 중심, 개조식 문체 사용, 1~2문장 요약)"
}}
"""
    try:
        with st.spinner("AI가 피드백과 생기부 초안을 분석하고 있습니다..."):
            response = model.generate_content(prompt)
            # JSON 파싱
            result = json.loads(response.text)
            feedback = result.get("feedback", "피드백을 생성하지 못했습니다.")
            record_suggestion = result.get("record_suggestion", "생기부 초안을 생성하지 못했습니다.")
        return feedback, record_suggestion
    except json.JSONDecodeError:
        st.error("AI가 유효한 JSON 형식으로 응답하지 않았습니다. 일반 텍스트로 결과를 표시합니다.")
        return response.text, "생기부 초안 생성에 실패했습니다 (JSON 파싱 오류)."
    except Exception as e:
        st.error(f"Gemini API 호출 중 오류가 발생했습니다: {e}")
        return "피드백 생성 중 오류가 발생했습니다.", "생기부 초안 생성 중 오류가 발생했습니다."


def get_overall_assessment(model, class_name, student_id, all_submissions_text):
    prompt = f"""
당신은 대한민국 고등학교 교사로서, 학생 한 명의 특정 과목 활동 전체를 종합하여 학교생활기록부 '세부능력 및 특기사항'에 기록할 최종 평가 의견을 작성해야 합니다.

[과목명/활동명]
{class_name}

[학생 ID]
{student_id}

[학생이 제출한 전체 활동 내용]
{all_submissions_text}

[작성 지침]
1.  **종합적 분석**: 학생이 제출한 모든 내용을 종합적으로 분석하여, 학생의 지적 호기심, 탐구 과정, 사고의 깊이, 성장 과정이 드러나도록 서술해주세요.
2.  **핵심 역량 강조**: 활동 전반에서 일관되게 나타나는 학생의 핵심 역량(예: 논리적 분석력, 창의적 접근, 정보 종합 능력, 비판적 사고력 등)을 구체적인 활동 내용을 근거로 제시해주세요.
3.  **과정 중심 서술**: '무엇을 제출했다'는 결과 나열이 아닌, '어떤 아이디어에서 출발하여 어떤 과정을 거쳐 생각을 발전시켰는지'가 드러나도록 작성해주세요.
4.  **객관적이고 구체적인 서술**: '뛰어남', '우수함'과 같은 주관적 표현을 지양하고, 학생의 활동을 구체적으로 묘사하여 강점이 자연스럽게 드러나게 해주세요.
5.  **문체 및 형식**: '~함.', '~음.'으로 끝나는 개조식 문체를 사용하고, 전체 내용은 2~4개의 문장으로 간결하게 요약해주세요.
6.  **결과물 형식**: 반드시 다음 JSON 형식에 맞춰 최종 평가 의견만 한 번에 출력해주세요. {{ "assessment": "여기에 최종 평가 의견을 작성합니다." }}
"""
    try:
        with st.spinner("AI가 학생의 모든 활동을 종합하여 총평을 생성하고 있습니다..."):
            response = model.generate_content(prompt)
            result = json.loads(response.text)
            return result.get("assessment", "총평을 생성하지 못했습니다.")
    except Exception as e:
        st.error(f"종합 평가 의견 생성 중 API 오류가 발생했습니다: {e}")
        return "종합 평가 의견 생성에 실패했습니다."

# ----------------------------------------------------------------------
# UI 렌더링 함수
# ----------------------------------------------------------------------
def student_view(submissions_sheet, docs_service, model):
    st.sidebar.success(f"{st.session_state['user_id']}님, 환영합니다.")
    logout()
    st.sidebar.markdown("---")
    CLASS_LIST = {
        "자유 낙하와 수평 방향으로 던진 물체의 운동 비교" : "1AnUqkNgFwO6EwX3p3JaVhk8bOT7-TONIdT9sl-lis_U",
        "전자기 유도" : "1U9nOSDH3EXF0dX0rvkpiTfk7w61Wy90PDWf-uM9QnHY"
    }
    if 'current_class' not in st.session_state: st.session_state.current_class = ""
    class_name = st.sidebar.radio("수업 선택", list(CLASS_LIST.keys()), key="class_selector")
    if class_name != st.session_state.current_class:
        st.session_state.current_class = class_name
        st.session_state.submission_content, st.session_state.feedback = load_previous_submission(submissions_sheet, st.session_state['user_id'], class_name)
        if 'overall_assessment' in st.session_state: del st.session_state['overall_assessment']
    doc_id = CLASS_LIST[class_name]
    template_text = get_doc_content(docs_service, doc_id)
    if not template_text: st.stop()
    activities = parse_template_by_activity(template_text)
    if not activities: st.warning("템플릿에서 '## ' 활동을 찾을 수 없습니다."); st.stop()
    st.sidebar.markdown("---")
    if 'current_activity' not in st.session_state: st.session_state.current_activity = ""
    selected_activity_title = st.sidebar.radio("활동 선택", list(activities.keys()), key="activity_selector")
    if selected_activity_title != st.session_state.current_activity:
        st.session_state.current_activity = selected_activity_title
        if 'feedback' in st.session_state: del st.session_state['feedback']
    st.header(f"📝 {class_name}")
    st.subheader(selected_activity_title)
    st.markdown("---")
    activity_data = activities[selected_activity_title]
    for part in activity_data['parts']:
        if part['type'] == 'static':
            st.markdown(part['content'], unsafe_allow_html=True)
        elif part['type'] == 'input':
            label = part['label']
            st.session_state.submission_content[label] = st.text_area(
                label=label,
                value=st.session_state.submission_content.get(label, ""),
                placeholder=part['placeholder'],
                height=250,
                key=f"{class_name}_{label}"
            )
    st.markdown("---")
    if st.button("전체 내용 저장 및 AI 피드백 받기", type="primary"):
        if any(st.session_state.submission_content.values()):
            all_exemplars = "\n\n".join([f"### {title}\n{data['exemplar']}" for title, data in activities.items() if data['exemplar']])
            feedback, record_suggestion = get_ai_feedback(model, class_name, st.session_state.submission_content, all_exemplars)
            st.session_state.feedback = feedback
            save_submission(submissions_sheet, st.session_state['user_id'], class_name, st.session_state.submission_content, feedback, record_suggestion)
            st.success("저장 및 피드백 생성이 완료되었습니다!")
        else:
            st.warning("제출할 내용이 없습니다.")
    if 'feedback' in st.session_state and st.session_state.feedback:
        with st.expander("🤖 AI 피드백 보기", expanded=True):
            st.markdown(st.session_state.feedback)

def teacher_dashboard(submissions_sheet, model):
    st.sidebar.warning(f"🧑‍🏫 교사 모드")
    logout()
    st.sidebar.markdown("---")
    st.header("교사 대시보드")
    submissions_df = pd.DataFrame(submissions_sheet.get_all_records())
    if submissions_df.empty:
        st.info("아직 제출된 학생 데이터가 없습니다.")
        st.stop()
    CLASS_LIST = list(submissions_df['class_name'].unique())
    selected_class = st.sidebar.selectbox("수업 선택", CLASS_LIST)
    if selected_class:
        students_in_class = list(submissions_df[submissions_df['class_name'] == selected_class]['student_id'].unique())
        selected_student = st.sidebar.selectbox("학생 선택", students_in_class)
        if selected_student:
            st.subheader(f"'{selected_class}' 수업에 대한 {selected_student} 학생의 제출 내용")
            student_submission = submissions_df[
                (submissions_df['student_id'].astype(str) == str(selected_student)) &
                (submissions_df['class_name'] == selected_class)
            ].sort_values(by='timestamp', ascending=False).iloc[0]
            submission_content = json.loads(student_submission['submission_content'])
            feedback = student_submission['feedback']
            with st.expander("학생 제출 원본 및 개별 피드백 보기", expanded=False):
                for activity, content in submission_content.items():
                    st.markdown(f"**- {activity}**")
                    st.text_area("", value=content, height=150, disabled=True, key=f"view_{activity}")
                st.markdown("---")
                st.markdown("**AI 개별 피드백**")
                st.markdown(feedback)
            st.markdown("---")
            st.subheader("종합 평가 의견 (생기부용)")
            if st.button("선택 학생 총평 생성하기", type="primary"):
                all_submissions_text = ""
                for activity, content in submission_content.items():
                    all_submissions_text += f"### {activity}\n{content}\n\n"
                assessment = get_overall_assessment(model, selected_class, selected_student, all_submissions_text)
                st.session_state['overall_assessment'] = assessment
            if 'overall_assessment' in st.session_state and st.session_state.overall_assessment:
                st.markdown(st.session_state['overall_assessment'])

# ----------------------------------------------------------------------
# 메인 실행 로직
# ----------------------------------------------------------------------
def main():
    gs, docs_service, model = setup_connections()
    if not all([gs, docs_service, model]): st.stop()

    users_sheet = get_sheet(gs, "users")
    submissions_sheet = get_sheet(gs, "submissions")
    if not users_sheet or not submissions_sheet: st.stop()

    if 'logged_in' not in st.session_state or not st.session_state['logged_in']:
        login(users_sheet)
    else:
        if st.session_state.get('is_teacher', False):
            teacher_dashboard(submissions_sheet, model)
        elif st.session_state.get('password_needs_change', False):
            change_password_view(users_sheet)
        else:
            student_view(submissions_sheet, docs_service, model)

if __name__ == "__main__":
    main()


