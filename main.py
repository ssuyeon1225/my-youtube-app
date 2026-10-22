
import streamlit as st
import pandas as pd
import requests
import re
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
st.write("유튜브 영상의 댓글을 수집하고 좋아요가 많은 순서대로 확인합니다.")
st.caption("1단계 · YouTube Data API v3를 이용한 댓글 수집")


# ==========================================
# 2. 예시 영상 주소 설정
# ==========================================

EXAMPLE_1 = "https://youtu.be/d95J8yzvjbQ?si=LfL5DLwCL8Pk077r"
EXAMPLE_2 = "https://youtu.be/I9vK5EVTt0U?si=NEZ8L7MRuNvrzINa"

# session_state는 버튼을 눌러도 입력값을 유지하는 저장 공간입니다.
if "youtube_url" not in st.session_state:
    st.session_state.youtube_url = EXAMPLE_1


# ==========================================
# 3. 유튜브 영상 ID 추출 함수
# ==========================================

def extract_video_id(url):
    """
    유튜브 주소에서 11자리 영상 ID를 추출합니다.

    지원하는 주소:
    - https://youtu.be/영상ID
    - https://www.youtube.com/watch?v=영상ID
    - https://youtube.com/shorts/영상ID
    - https://www.youtube.com/live/영상ID

    주소 뒤에 붙는 si 등의 추적 파라미터는 무시합니다.
    """

    url = url.strip()

    if not url:
        return None

    # www. 또는 https://가 없는 주소도 처리합니다.
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        parsed = urlparse(url)

        # 주소의 도메인을 확인합니다.
        domain = (parsed.hostname or "").lower()

        video_id = None

        # youtu.be 짧은 주소
        if domain == "youtu.be":
            video_id = parsed.path.strip("/").split("/")[0]

        # 일반 유튜브 주소
        elif domain in (
            "youtube.com",
            "www.youtube.com",
            "m.youtube.com",
            "music.youtube.com"
        ):

            # watch?v=영상ID
            if parsed.path.rstrip("/") == "/watch":
                query = parse_qs(parsed.query)
                video_id = query.get("v", [None])[0]

            # shorts, live, embed 주소
            else:
                match = re.match(
                    r"^/(shorts|live|embed)/([^/]+)",
                    parsed.path
                )

                if match:
                    video_id = match.group(2)

        # 유튜브 영상 ID는 영문, 숫자, _, -로 구성된 11자리입니다.
        if video_id and re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            return video_id

        return None

    except (ValueError, TypeError):
        return None


# ==========================================
# 4. 유튜브 댓글 수집 함수
# ==========================================

def get_youtube_comments(video_id, api_key):
    """
    YouTube Data API v3의 commentThreads를 이용합니다.

    - part: snippet
    - maxResults: 100
    - order: relevance

    댓글 원문은 textOriginal,
    좋아요 수는 likeCount를 사용합니다.
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

    # requests로 API에 요청을 보냅니다.
    response = requests.get(
        api_url,
        params=params,
        timeout=20
    )

    # API 요청이 실패했을 때 오류를 확인합니다.
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

        # 댓글이 비활성화된 영상
        if reason == "commentsDisabled":
            raise ValueError(
                "이 영상은 댓글 기능이 비활성화되어 있습니다."
            )

        # 영상을 찾을 수 없는 경우
        elif reason in ("videoNotFound", "notFound"):
            raise ValueError(
                "영상을 찾을 수 없습니다. 링크를 확인해 주세요."
            )

        # API 키에 문제가 있는 경우
        elif response.status_code in (401, 403):
            raise ValueError(
                "YouTube API 요청이 거부되었습니다. "
                "API 키, API 활성화 여부, 할당량을 확인해 주세요. "
                f"상세 사유: {reason or message or '확인 불가'}"
            )

        # 그 밖의 오류
        else:
            raise ValueError(
                "댓글을 가져오는 중 문제가 발생했습니다. "
                f"(HTTP {response.status_code}) "
                f"{message}"
            )

    data = response.json()

    comments = []

    # API 응답에서 댓글 정보를 하나씩 꺼냅니다.
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

    # 리스트를 판다스 데이터프레임으로 변환합니다.
    df = pd.DataFrame(
        comments,
        columns=["댓글", "좋아요 수"]
    )

    # 좋아요가 많은 순서대로 정렬합니다.
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
# 5. 예시 영상 선택 버튼
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
# 6. 유튜브 링크 입력창
# ==========================================

youtube_url = st.text_input(
    "유튜브 영상 링크를 입력하세요.",
    key="youtube_url",
    placeholder="https://www.youtube.com/watch?v=..."
)

# 입력된 주소에서 영상 ID를 추출합니다.
video_id = extract_video_id(youtube_url)

if youtube_url.strip():
    if video_id:
        st.success(f"영상 ID 확인 완료: {video_id}")
    else:
        st.warning(
            "올바른 유튜브 영상 링크를 입력해 주세요."
        )


# ==========================================
# 7. 댓글 수집 버튼
# ==========================================

st.subheader("2. 댓글 수집")

st.info(
    "YouTube Data API v3를 사용하여 "
    "관련성 높은 상위 댓글을 최대 100개 가져옵니다. "
    "수집 후 좋아요 수 기준으로 정렬합니다."
)

if st.button(
    "🔍 댓글 100개 가져오기",
    type="primary",
    use_container_width=True
):

    # 이전에 수집한 댓글이 있다면 초기화합니다.
    st.session_state.pop("comments_df", None)
    st.session_state.pop("collected_video_id", None)

    if not youtube_url.strip():
        st.warning("먼저 유튜브 영상 링크를 입력해 주세요.")

    elif not video_id:
        st.error(
            "유튜브 영상 ID를 추출할 수 없습니다. "
            "주소를 다시 확인해 주세요."
        )

    else:
        # Streamlit Cloud의 비밀 금고에서 API 키를 읽습니다.
        try:
            api_key = st.secrets["YOUTUBE_API_KEY"]

        except (KeyError, FileNotFoundError):
            api_key = None

        if not api_key:
            st.error(
                "YouTube API 키가 설정되지 않았습니다. "
                "Streamlit Cloud의 Secrets에 "
                "YOUTUBE_API_KEY를 등록해 주세요."
            )

        else:
            # 댓글을 가져오는 동안 로딩 메시지를 보여줍니다.
            with st.spinner("유튜브 댓글을 가져오는 중입니다..."):

                try:
                    df = get_youtube_comments(
                        video_id,
                        api_key
                    )

                    # 수집 결과를 세션에 저장합니다.
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
# 8. 수집 결과 표시
# ==========================================

if "comments_df" in st.session_state:

    df = st.session_state.comments_df

    st.divider()
    st.subheader("3. 댓글 수집 결과")

    if df.empty:
        st.warning(
            "가져온 댓글이 없습니다. "
            "댓글이 없는 영상이거나 "
            "댓글을 공개적으로 조회할 수 없는 영상일 수 있습니다."
        )

    else:
        st.success("댓글 수집이 완료되었습니다.")

        # 지표 카드: 수집한 댓글 개수
        st.metric(
            label="수집한 댓글 수",
            value=f"{len(df):,}개"
        )

        st.caption(
            f"영상 ID: {st.session_state.collected_video_id} "
            "| 정렬 기준: 좋아요 수 내림차순"
        )

        # 댓글 표 출력
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
            "※ 수집 대상은 관련성 기준으로 API가 반환한 "
            "최대 100개의 최상위 댓글입니다. "
            "영상의 전체 댓글 중 좋아요 상위 100개를 "
            "보장하지는 않습니다."
        )
