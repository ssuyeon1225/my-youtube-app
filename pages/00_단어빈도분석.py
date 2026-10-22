
import streamlit as st
import pandas as pd
import requests
import re
import plotly.express as px

from collections import Counter
from urllib.parse import urlparse, parse_qs


# ==========================================
# 1. 스트림릿 페이지 기본 설정
# ==========================================

st.set_page_config(
    page_title="유튜브 댓글 분석기",
    page_icon="💬",
    layout="wide"
)

st.title("💬 유튜브 댓글 분석기")
st.write("유튜브 댓글을 수집하고 자주 등장하는 단어를 분석합니다.")
st.caption("1단계: 댓글 수집 | 2단계: 단어 빈도 분석")


# ==========================================
# 2. 예시 영상 주소 설정
# ==========================================

EXAMPLE_1 = "https://youtu.be/d95J8yzvjbQ?si=LfL5DLwCL8Pk077r"
EXAMPLE_2 = "https://youtu.be/I9vK5EVTt0U?si=NEZ8L7MRuNvrzINa"

# 버튼을 눌러도 입력값이 유지되도록 설정합니다.
if "youtube_url" not in st.session_state:
    st.session_state.youtube_url = EXAMPLE_1


# ==========================================
# 3. 유튜브 영상 ID 추출 함수
# ==========================================

def extract_video_id(url):
    """
    유튜브 주소에서 11자리 영상 ID를 추출합니다.

    지원하는 주소:
    - youtu.be/영상ID
    - youtube.com/watch?v=영상ID
    - youtube.com/shorts/영상ID
    - youtube.com/live/영상ID

    si= 등의 추가 파라미터는 무시합니다.
    """

    url = url.strip()

    if not url:
        return None

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        parsed = urlparse(url)
        domain = (parsed.hostname or "").lower()

        video_id = None

        # 짧은 주소
        if domain == "youtu.be":
            video_id = parsed.path.strip("/").split("/")[0]

        # 일반 유튜브 주소
        elif domain in (
            "youtube.com",
            "www.youtube.com",
            "m.youtube.com",
            "music.youtube.com"
        ):

            if parsed.path.rstrip("/") == "/watch":
                query = parse_qs(parsed.query)
                video_id = query.get("v", [None])[0]

            else:
                match = re.match(
                    r"^/(shorts|live|embed)/([^/]+)",
                    parsed.path
                )

                if match:
                    video_id = match.group(2)

        # 유튜브 영상 ID는 11자리입니다.
        if video_id and re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            return video_id

        return None

    except (ValueError, TypeError):
        return None


# ==========================================
# 4. 유튜브 댓글 수집 함수 (1단계)
# ==========================================

def get_youtube_comments(video_id, api_key):
    """
    YouTube Data API v3의 commentThreads를 사용합니다.

    관련성 기준으로 최대 100개의 최상위 댓글을 가져온 뒤
    좋아요 수가 많은 순서대로 정렬합니다.
    """

    api_url = (
        "https://www.googleapis.com/youtube/v3/commentThreads"
    )

    params = {
        "part": "snippet",
        "videoId": video_id,
        "maxResults": 100,
        "order": "relevance",
        "textFormat": "plainText",
        "key": api_key
    }

    response = requests.get(
        api_url,
        params=params,
        timeout=20
    )

    # API 요청 실패 시 오류 메시지를 확인합니다.
    if response.status_code != 200:

        try:
            error_data = response.json()
            error_info = error_data.get("error", {})
            errors = error_info.get("errors", [])

            reason = (
                errors[0].get("reason", "")
                if errors else ""
            )

            message = error_info.get("message", "")

        except ValueError:
            reason = ""
            message = ""

        if reason == "commentsDisabled":
            raise ValueError(
                "이 영상은 댓글 기능이 비활성화되어 있습니다."
            )

        elif reason in ("videoNotFound", "notFound"):
            raise ValueError(
                "영상을 찾을 수 없습니다. 링크를 확인해 주세요."
            )

        elif response.status_code in (401, 403):
            raise ValueError(
                "YouTube API 요청이 거부되었습니다. "
                "API 키와 할당량을 확인해 주세요. "
                f"상세 사유: {reason or message or '확인 불가'}"
            )

        else:
            raise ValueError(
                "댓글을 가져오는 중 문제가 발생했습니다. "
                f"(HTTP {response.status_code}) {message}"
            )

    data = response.json()

    comments = []

    # API 응답에서 댓글 원문과 좋아요 수를 추출합니다.
    for item in data.get("items", []):

        snippet = item.get(
            "snippet", {}
        ).get(
            "topLevelComment", {}
        ).get(
            "snippet", {}
        )

        comment_text = snippet.get("textOriginal", "")
        like_count = snippet.get("likeCount", 0)

        comments.append({
            "댓글": comment_text,
            "좋아요 수": like_count
        })

    # 데이터프레임으로 변환합니다.
    df = pd.DataFrame(
        comments,
        columns=["댓글", "좋아요 수"]
    )

    if not df.empty:

        df["좋아요 수"] = pd.to_numeric(
            df["좋아요 수"],
            errors="coerce"
        ).fillna(0).astype(int)

        df = df.sort_values(
            by="좋아요 수",
            ascending=False
        ).reset_index(drop=True)

    return df


# ==========================================
# 5. 댓글 단어 빈도 분석 함수 (2단계)
# ==========================================

def analyze_word_frequency(df):
    """
    댓글 전체에서 단어를 추출하고 빈도를 계산합니다.

    처리 과정:
    1. 댓글을 하나의 텍스트로 합치기
    2. 영문을 소문자로 변환
    3. 한글과 영문 단어 추출
    4. 한 글자 단어 제외
    5. 출현 빈도 계산
    6. 상위 20개 추출
    """

    # 댓글이 없다면 빈 데이터프레임을 반환합니다.
    if df.empty:
        return pd.DataFrame(
            columns=["단어", "빈도"]
        )

    # 모든 댓글을 하나의 문자열로 합칩니다.
    all_text = " ".join(
        df["댓글"].fillna("").astype(str)
    )

    # 영어 대문자와 소문자를 같은 단어로 처리합니다.
    all_text = all_text.lower()

    # 정규표현식으로 한글과 영문 단어를 추출합니다.
    # 숫자와 특수문자는 제외합니다.
    words = re.findall(
        r"[가-힣a-z]+",
        all_text
    )

    # 한 글자 단어는 제외합니다.
    words = [
        word for word in words
        if len(word) >= 2
    ]

    # Counter로 각 단어가 몇 번 등장했는지 계산합니다.
    word_counts = Counter(words)

    # 가장 자주 등장한 단어 20개를 추출합니다.
    top_20 = word_counts.most_common(20)

    # 분석 결과를 데이터프레임으로 변환합니다.
    word_df = pd.DataFrame(
        top_20,
        columns=["단어", "빈도"]
    )

    return word_df


# ==========================================
# 6. 예시 영상 선택 버튼
# ==========================================

st.subheader("1. 분석할 영상 선택")

col1, col2 = st.columns(2)

with col1:
    if st.button(
        "예시 1 · 딥마인드 다큐(영어 댓글)",
        use_container_width=True
    ):
        st.session_state.youtube_url = EXAMPLE_1

with col2:
    if st.button(
        "예시 2 · 2002 월드컵 추억(한국어 댓글)",
        use_container_width=True
    ):
        st.session_state.youtube_url = EXAMPLE_2


# ==========================================
# 7. 유튜브 링크 입력창
# ==========================================

youtube_url = st.text_input(
    "유튜브 영상 링크를 입력하세요.",
    key="youtube_url",
    placeholder="https://www.youtube.com/watch?v=..."
)

video_id = extract_video_id(youtube_url)

if youtube_url.strip():

    if video_id:
        st.success(f"영상 ID 확인 완료: {video_id}")

    else:
        st.warning(
            "올바른 유튜브 영상 링크를 입력해 주세요."
        )


# ==========================================
# 8. 댓글 수집 버튼
# ==========================================

st.subheader("2. 댓글 수집")

st.info(
    "관련성 기준으로 최대 100개의 최상위 댓글을 가져온 뒤 "
    "좋아요 수 기준으로 정렬합니다."
)

if st.button(
    "🔍 댓글 100개 가져오기",
    type="primary",
    use_container_width=True
):

    # 이전 분석 결과를 초기화합니다.
    st.session_state.pop("comments_df", None)
    st.session_state.pop("collected_video_id", None)

    if not youtube_url.strip():

        st.warning(
            "먼저 유튜브 영상 링크를 입력해 주세요."
        )

    elif not video_id:

        st.error(
            "유튜브 영상 ID를 추출할 수 없습니다. "
            "주소를 다시 확인해 주세요."
        )

    else:

        # Streamlit Secrets에서 API 키를 불러옵니다.
        try:
            api_key = st.secrets["YOUTUBE_API_KEY"]

        except (KeyError, FileNotFoundError):
            api_key = None

        if not api_key:

            st.error(
                "YouTube API 키가 설정되지 않았습니다. "
                "Streamlit Cloud의 Secrets에서 "
                "YOUTUBE_API_KEY를 등록해 주세요."
            )

        else:

            with st.spinner("유튜브 댓글을 가져오는 중입니다..."):

                try:

                    df = get_youtube_comments(
                        video_id,
                        api_key
                    )

                    # 수집한 댓글을 세션에 저장합니다.
                    st.session_state.comments_df = df
                    st.session_state.collected_video_id = video_id

                except requests.exceptions.Timeout:

                    st.error(
                        "요청 시간이 초과되었습니다. "
                        "잠시 후 다시 시도해 주세요."
                    )

                except requests.exceptions.RequestException:

                    st.error(
                        "네트워크 연결에 문제가 발생했습니다. "
                        "인터넷 연결 상태를 확인해 주세요."
                    )

                except ValueError as e:

                    st.error(str(e))

                except Exception:

                    st.error(
                        "예상하지 못한 오류가 발생했습니다. "
                        "잠시 후 다시 시도해 주세요."
                    )


# ==========================================
# 9. 댓글 수집 결과 표시 (1단계)
# ==========================================

if "comments_df" in st.session_state:

    df = st.session_state.comments_df

    st.divider()
    st.subheader("3. 댓글 수집 결과")

    if df.empty:

        st.warning(
            "가져온 댓글이 없습니다. "
            "댓글이 없는 영상이거나 "
            "공개적으로 조회할 수 없는 영상일 수 있습니다."
        )

    else:

        st.success("댓글 수집이 완료되었습니다.")

        # 수집한 댓글 개수를 지표 카드로 표시합니다.
        st.metric(
            label="수집한 댓글 수",
            value=f"{len(df):,}개"
        )

        st.caption(
            f"영상 ID: {st.session_state.collected_video_id} "
            "| 정렬 기준: 좋아요 수 내림차순"
        )

        # 댓글 원문과 좋아요 수를 표로 보여줍니다.
        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "댓글": st.column_config.TextColumn(
                    "댓글 원문",
                    width="large"
                ),
                "좋아요 수": st.column_config.NumberColumn(
                    "좋아요 수",
                    format="%d"
                )
            }
        )

        st.caption(
            "※ 관련성 기준으로 수집한 최대 100개의 댓글입니다. "
            "영상 전체의 좋아요 상위 100개를 의미하지는 않습니다."
        )


        # ==========================================
        # 10. 단어 빈도 분석 (2단계)
        # ==========================================

        st.divider()
        st.subheader("4. 댓글 단어 빈도 분석 TOP 20")

        st.write(
            "수집한 댓글 전체에서 자주 등장한 단어를 "
            "분석한 결과입니다."
        )

        # 앞에서 정의한 함수를 실행합니다.
        word_df = analyze_word_frequency(df)

        if word_df.empty:

            st.warning(
                "분석할 단어가 없습니다. "
                "두 글자 이상의 한글 또는 영어 단어가 "
                "포함되어 있는지 확인해 주세요."
            )

        else:

            # 가장 많이 등장한 단어를 지표 카드로 표시합니다.
            top_word = word_df.iloc[0]["단어"]
            top_count = int(word_df.iloc[0]["빈도"])

            metric1, metric2 = st.columns(2)

            with metric1:
                st.metric(
                    label="가장 많이 등장한 단어",
                    value=top_word
                )

            with metric2:
                st.metric(
                    label="해당 단어 출현 횟수",
                    value=f"{top_count:,}회"
                )

            # ==========================================
            # 11. Plotly 가로 막대그래프
            # ==========================================

            # Plotly Express로 가로 막대그래프를 만듭니다.
            fig = px.bar(
                word_df,
                x="빈도",
                y="단어",
                orientation="h",
                text="빈도",
                title="댓글에서 자주 등장한 단어 TOP 20",
                labels={
                    "빈도": "출현 횟수",
                    "단어": "단어"
                }
            )

            # 빈도가 가장 높은 단어가 맨 위에 오도록 설정합니다.
            fig.update_yaxes(
                categoryorder="total ascending"
            )

            # 그래프 디자인을 조정합니다.
            fig.update_traces(
                textposition="outside",
                cliponaxis=False
            )

            fig.update_layout(
                height=650,
                margin=dict(
                    l=30,
                    r=80,
                    t=70,
                    b=40
                ),
                xaxis_title="출현 횟수",
                yaxis_title="",
                showlegend=False
            )

            # Streamlit 화면에 그래프를 출력합니다.
            st.plotly_chart(
                fig,
                use_container_width=True
            )

            # ==========================================
            # 12. 단어 빈도표 출력
            # ==========================================

            st.subheader("단어 빈도표")

            st.dataframe(
                word_df,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "단어": st.column_config.TextColumn(
                        "단어"
                    ),
                    "빈도": st.column_config.NumberColumn(
                        "출현 횟수",
                        format="%d"
                    )
                }
            )

            st.caption(
                "분석 기준: 영문 소문자 통일, "
                "한글·영문 단어 추출, "
                "한 글자 단어 및 숫자·특수문자 제외. "
                "불용어 제거와 형태소 분석은 적용하지 않았습니다."
            )
