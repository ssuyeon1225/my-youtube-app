
import streamlit as st
import pandas as pd
import requests
import re
import plotly.express as px

from collections import Counter
from urllib.parse import urlparse, parse_qs
from wordcloud import WordCloud
from pathlib import Path
from io import BytesIO


# ==========================================
# 1. 스트림릿 페이지 기본 설정
# ==========================================

st.set_page_config(
    page_title="유튜브 댓글 분석기",
    page_icon="💬",
    layout="wide"
)

st.title("💬 유튜브 댓글 분석기")

st.write(
    "유튜브 댓글을 수집하고 단어 빈도와 "
    "워드클라우드를 분석합니다."
)

st.caption(
    "1단계: 댓글 수집 | "
    "2단계: 단어 빈도 분석 | "
    "3단계: 워드클라우드"
)


# ==========================================
# 2. 예시 영상 주소 설정
# ==========================================

EXAMPLE_1 = (
    "https://youtu.be/d95J8yzvjbQ"
    "?si=LfL5DLwCL8Pk077r"
)

EXAMPLE_2 = (
    "https://youtu.be/I9vK5EVTt0U"
    "?si=NEZ8L7MRuNvrzINa"
)

# 버튼을 눌러도 입력값을 유지합니다.
if "youtube_url" not in st.session_state:
    st.session_state.youtube_url = EXAMPLE_1


# ==========================================
# 3. 유튜브 영상 ID 추출 함수
# ==========================================

def extract_video_id(url):
    """
    유튜브 링크에서 영상 ID를 추출합니다.

    지원 주소:
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

        if domain == "youtu.be":
            video_id = parsed.path.strip("/").split("/")[0]

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

        # 영상 ID는 영문, 숫자, _, -로 구성된 11자리입니다.
        if video_id and re.fullmatch(
            r"[A-Za-z0-9_-]{11}",
            video_id
        ):
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

    관련성 기준으로 최대 100개의 최상위 댓글을
    가져온 뒤 좋아요 수 기준으로 정렬합니다.
    """

    api_url = (
        "https://www.googleapis.com/youtube/v3/"
        "commentThreads"
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

    # API 오류가 발생했을 때 처리합니다.
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
                "영상을 찾을 수 없습니다. "
                "링크를 확인해 주세요."
            )

        elif response.status_code in (401, 403):
            raise ValueError(
                "YouTube API 요청이 거부되었습니다. "
                "API 키, API 활성화 여부, 할당량을 확인해 주세요. "
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

    df = pd.DataFrame(
        comments,
        columns=["댓글", "좋아요 수"]
    )

    # 좋아요 수가 많은 순서대로 정렬합니다.
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
# 5. 단어 추출 함수 (2단계 + 3단계 공통)
# ==========================================

def extract_words(df):
    """
    댓글 전체를 단어로 나누는 함수입니다.

    1. 모든 댓글 합치기
    2. 영어 소문자로 통일
    3. 한글과 영어 단어 추출
    4. 한 글자 단어 제외

    2단계와 3단계에서 공통으로 사용합니다.
    """

    if df.empty:
        return []

    # 모든 댓글을 하나의 문자열로 합칩니다.
    all_text = " ".join(
        df["댓글"].fillna("").astype(str)
    )

    # 영어 대소문자를 통일합니다.
    all_text = all_text.lower()

    # 한글과 영어로 이루어진 단어를 추출합니다.
    # 숫자와 특수문자는 제외합니다.
    words = re.findall(
        r"[가-힣a-z]+",
        all_text
    )

    # 한 글자 단어를 제외합니다.
    words = [
        word for word in words
        if len(word) >= 2
    ]

    return words


# ==========================================
# 6. 단어 빈도 분석 함수 (2단계)
# ==========================================

def analyze_word_frequency(words):
    """
    추출한 단어의 출현 횟수를 계산하고
    가장 많이 등장한 단어 20개를 반환합니다.
    """

    if not words:
        return pd.DataFrame(
            columns=["단어", "빈도"]
        )

    # 각 단어가 몇 번 등장했는지 계산합니다.
    word_counts = Counter(words)

    # 빈도가 높은 상위 20개를 추출합니다.
    top_20 = word_counts.most_common(20)

    word_df = pd.DataFrame(
        top_20,
        columns=["단어", "빈도"]
    )

    return word_df


# ==========================================
# 7. 한글 폰트 다운로드 함수 (3단계)
# ==========================================

FONT_URL = (
    "https://raw.githubusercontent.com/google/fonts/"
    "main/ofl/nanumgothic/NanumGothic-Regular.ttf"
)

FONT_PATH = Path("/tmp/NanumGothic-Regular.ttf")


@st.cache_resource(show_spinner=False)
def download_korean_font():
    """
    워드클라우드에서 한글이 깨지지 않도록
    나눔고딕 폰트를 다운로드합니다.

    이미 다운로드한 폰트가 있다면 재사용합니다.
    다운로드에 실패하면 오류를 발생시킵니다.
    """

    # 폰트 파일이 이미 있으면 재사용합니다.
    if FONT_PATH.exists() and FONT_PATH.stat().st_size > 0:
        return str(FONT_PATH)

    try:
        response = requests.get(
            FONT_URL,
            timeout=20
        )

        response.raise_for_status()

        # 다운로드한 파일이 실제 TTF 폰트인지 확인합니다.
        # 일반적인 TrueType 폰트는 이 바이트로 시작합니다.
        if not response.content.startswith(b"\x00\x01\x00\x00"):
            raise ValueError(
                "다운로드한 파일이 올바른 TTF 폰트가 아닙니다."
            )

        # 폰트 파일을 임시 저장 공간에 저장합니다.
        FONT_PATH.write_bytes(response.content)

        return str(FONT_PATH)

    except (
        requests.exceptions.RequestException,
        OSError,
        ValueError
    ) as e:

        raise RuntimeError(
            "한글 폰트 다운로드에 실패했습니다. "
            "인터넷 연결 상태를 확인한 뒤 "
            "잠시 후 다시 시도해 주세요."
        ) from e


# ==========================================
# 8. 워드클라우드 생성 함수 (3단계)
# ==========================================

def create_wordcloud(words, font_path):
    """
    댓글 전체의 단어 빈도를 이용해
    워드클라우드 이미지를 만듭니다.

    - 흰색 배경
    - 나눔고딕 폰트
    - 한 글자 단어 제외
    - matplotlib 사용하지 않음
    """

    # 모든 단어의 출현 빈도를 계산합니다.
    word_counts = Counter(words)

    # WordCloud 객체를 생성합니다.
    wc = WordCloud(
        font_path=font_path,
        width=1200,
        height=650,
        background_color="white",
        max_words=200,
        min_font_size=10,
        max_font_size=160,
        prefer_horizontal=0.9,
        collocations=False,
        random_state=42
    )

    # 단어별 빈도를 이용해 워드클라우드를 생성합니다.
    wc.generate_from_frequencies(word_counts)

    # WordCloud를 PIL 이미지로 변환합니다.
    # matplotlib은 사용하지 않습니다.
    image = wc.to_image()

    return image


# ==========================================
# 9. 예시 영상 선택 버튼
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
# 10. 유튜브 링크 입력창
# ==========================================

youtube_url = st.text_input(
    "유튜브 영상 링크를 입력하세요.",
    key="youtube_url",
    placeholder="https://www.youtube.com/watch?v=..."
)

video_id = extract_video_id(youtube_url)

if youtube_url.strip():

    if video_id:
        st.success(
            f"영상 ID 확인 완료: {video_id}"
        )

    else:
        st.warning(
            "올바른 유튜브 영상 링크를 입력해 주세요."
        )


# ==========================================
# 11. 댓글 수집 버튼
# ==========================================

st.subheader("2. 댓글 수집")

st.info(
    "관련성 기준으로 최대 100개의 최상위 댓글을 "
    "가져온 뒤 좋아요 수 기준으로 정렬합니다."
)

if st.button(
    "🔍 댓글 100개 가져오기",
    type="primary",
    use_container_width=True
):

    # 이전 댓글 수집 결과를 초기화합니다.
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

        # Streamlit Secrets에서 API 키를 읽습니다.
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

            with st.spinner(
                "유튜브 댓글을 가져오는 중입니다..."
            ):

                try:

                    df = get_youtube_comments(
                        video_id,
                        api_key
                    )

                    # 수집한 댓글을 저장합니다.
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
# 12. 댓글 수집 결과 표시 (1단계)
# ==========================================

if "comments_df" in st.session_state:

    df = st.session_state.comments_df

    st.divider()
    st.subheader("3. 댓글 수집 결과")

    if df.empty:

        st.warning(
            "가져온 댓글이 없습니다. "
            "댓글이 없는 영상이거나 공개적으로 "
            "조회할 수 없는 영상일 수 있습니다."
        )

    else:

        st.success("댓글 수집이 완료되었습니다.")

        st.metric(
            label="수집한 댓글 수",
            value=f"{len(df):,}개"
        )

        st.caption(
            f"영상 ID: {st.session_state.collected_video_id} "
            "| 정렬 기준: 좋아요 수 내림차순"
        )

        # 댓글 목록을 표로 보여줍니다.
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
            "※ 관련성 기준으로 수집한 최대 100개의 "
            "최상위 댓글입니다. 영상 전체의 좋아요 "
            "상위 100개를 의미하지는 않습니다."
        )

        # ==========================================
        # 13. 공통 단어 추출
        # ==========================================

        # 댓글 전체에서 단어를 한 번만 추출합니다.
        # 2단계와 3단계에서 같은 결과를 사용합니다.
        words = extract_words(df)

        # ==========================================
        # 14. 단어 빈도 분석 (2단계)
        # ==========================================

        st.divider()
        st.subheader("4. 댓글 단어 빈도 분석 TOP 20")

        st.write(
            "수집한 댓글 전체에서 자주 등장한 "
            "단어를 분석한 결과입니다."
        )

        word_df = analyze_word_frequency(words)

        if word_df.empty:

            st.warning(
                "분석할 단어가 없습니다. "
                "두 글자 이상의 한글 또는 영어 단어가 "
                "포함되어 있는지 확인해 주세요."
            )

        else:

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

            # Plotly 가로 막대그래프를 생성합니다.
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

            # 가장 많이 등장한 단어가 위에 오도록 설정합니다.
            fig.update_yaxes(
                categoryorder="total ascending"
            )

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

            st.plotly_chart(
                fig,
                use_container_width=True
            )

            st.subheader("단어 빈도표")

            st.dataframe(
                word_df,
                use_container_width=True,
                hide_index=True
            )

            st.caption(
                "분석 기준: 영문 소문자 통일, "
                "한글·영문 단어 추출, "
                "한 글자 단어 제외, "
                "숫자·특수문자 제외"
            )

        # ==========================================
        # 15. 워드클라우드 시각화 (3단계)
        # ==========================================

        st.divider()
        st.subheader("5. 댓글 워드클라우드")

        st.write(
            "댓글 전체에서 자주 등장한 단어를 "
            "크기에 따라 시각화합니다. "
            "많이 등장한 단어일수록 크게 표시됩니다."
        )

        if not words:

            st.warning(
                "워드클라우드를 만들 단어가 없습니다. "
                "두 글자 이상의 한글 또는 영어 단어가 "
                "필요합니다."
            )

        else:

            # 한글 폰트를 다운로드합니다.
            try:

                with st.spinner(
                    "한글 폰트를 준비하고 있습니다..."
                ):
                    font_path = download_korean_font()

            except RuntimeError as e:

                # 폰트 다운로드 실패 시 한국어로 안내합니다.
                st.error(str(e))
                font_path = None

            # 폰트를 정상적으로 준비했을 때만 실행합니다.
            if font_path is not None:

                try:

                    with st.spinner(
                        "워드클라우드를 생성하고 있습니다..."
                    ):

                        # 워드클라우드 이미지 생성
                        wordcloud_image = create_wordcloud(
                            words,
                            font_path
                        )

                    # 이미지를 화면에 바로 출력합니다.
                    # matplotlib을 사용하지 않습니다.
                    st.image(
                        wordcloud_image,
                        caption="유튜브 댓글 워드클라우드",
                        use_container_width=True
                    )

                    st.success(
                        "워드클라우드 생성이 완료되었습니다."
                    )

                    # ==========================================
                    # 16. 워드클라우드 PNG 다운로드
                    # ==========================================

                    # PIL 이미지를 PNG 파일 데이터로 변환합니다.
                    image_buffer = BytesIO()

                    wordcloud_image.save(
                        image_buffer,
                        format="PNG"
                    )

                    image_buffer.seek(0)

                    st.download_button(
                        label="📥 워드클라우드 PNG 다운로드",
                        data=image_buffer.getvalue(),
                        file_name="youtube_wordcloud.png",
                        mime="image/png",
                        use_container_width=True
                    )

                except Exception:

                    st.error(
                        "워드클라우드 이미지 생성에 실패했습니다. "
                        "잠시 후 다시 시도해 주세요."
                    )

        st.caption(
            "※ 워드클라우드는 수집된 댓글 전체의 "
            "단어 빈도를 기준으로 생성합니다. "
            "한 글자 단어는 제외하며, "
            "최대 200개의 단어를 표시합니다. "
            "한국어 형태소 분석과 불용어 제거는 "
            "적용하지 않았습니다."
        )
