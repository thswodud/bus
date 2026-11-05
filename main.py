from datetime import datetime, date, time, timedelta
from zoneinfo import ZoneInfo

import requests
import streamlit as st


# ------------------------------------------------------------
# 앱 기본 설정
# ------------------------------------------------------------
st.set_page_config(
    page_title="몇 시에 나갈까?",
    page_icon="🚌",
    layout="centered",
)

KST = ZoneInfo("Asia/Seoul")
KAKAO_LOCAL_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"
KAKAO_ADDRESS_URL = "https://dapi.kakao.com/v2/local/search/address.json"
KAKAO_TRANSIT_URL = "https://dapi.kakao.com/v2/routing/publictraffic"

st.markdown(
    """
    <style>
    .block-container {max-width: 850px; padding-top: 2rem; padding-bottom: 3rem;}
    .hero {padding: 1.5rem; border-radius: 18px; background: linear-gradient(120deg,#153c59,#167c80); color: white; margin-bottom: 1.2rem;}
    .hero h1 {color: white; margin: 0 0 .45rem 0; font-size: 2rem;}
    .hero p {color: white; opacity: .92; margin: 0;}
    .answer {padding: 1.3rem; border: 1px solid rgba(22,124,128,.35); border-radius: 16px; background: rgba(22,124,128,.07);}
    .answer-label {font-size: .95rem; opacity: .8; margin-bottom: .4rem;}
    .answer-time {font-size: 2.1rem; font-weight: 800; line-height: 1.25;}
    .muted {font-size: .88rem; color: #687782;}
    </style>
    """,
    unsafe_allow_html=True,
)


# ------------------------------------------------------------
# API 및 시간 계산 함수
# ------------------------------------------------------------
def get_api_key():
    """Streamlit Secrets에 저장된 카카오 REST API 키를 읽는다."""
    try:
        return str(st.secrets.get("KAKAO_REST_API_KEY", "")).strip()
    except Exception:
        return ""


def kakao_get(url, params, api_key):
    headers = {"Authorization": f"KakaoAK {api_key}"}
    try:
        response = requests.get(url, headers=headers, params=params, timeout=15)
    except requests.Timeout as exc:
        raise RuntimeError("카카오 API 응답 시간이 초과됐어요. 잠시 후 다시 시도해 주세요.") from exc
    except requests.RequestException as exc:
        raise RuntimeError("카카오 API에 연결하지 못했어요. 인터넷 연결을 확인해 주세요.") from exc

    if response.status_code == 401:
        raise RuntimeError("카카오 REST API 키가 올바르지 않아요. 앱의 REST API 키인지 확인해 주세요.")
    if response.status_code == 403:
        raise RuntimeError("카카오 API 권한이 없어요. 카카오 디벨로퍼스 앱 설정과 API 사용 권한을 확인해 주세요.")
    if response.status_code == 429:
        raise RuntimeError("API 요청 한도를 초과했어요. 잠시 후 다시 시도해 주세요.")
    if not response.ok:
        raise RuntimeError(f"카카오 API 오류가 발생했어요. (HTTP {response.status_code})")

    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError("카카오 API 응답을 JSON으로 읽을 수 없어요.") from exc


def search_place(query, api_key):
    """장소명으로 검색하고, 검색 결과가 없으면 주소 검색을 시도한다."""
    data = kakao_get(KAKAO_LOCAL_URL, {"query": query, "size": 5}, api_key)
    documents = data.get("documents", [])

    if documents:
        place = documents[0]
        return {
            "name": place.get("place_name") or query,
            "address": place.get("road_address_name") or place.get("address_name") or "주소 정보 없음",
            "x": float(place["x"]),
            "y": float(place["y"]),
        }

    address_data = kakao_get(KAKAO_ADDRESS_URL, {"query": query, "size": 5}, api_key)
    address_docs = address_data.get("documents", [])
    if address_docs:
        item = address_docs[0]
        address = item.get("road_address") or item.get("address") or {}
        return {
            "name": query,
            "address": address.get("address_name") or item.get("address_name") or query,
            "x": float(item["x"]),
            "y": float(item["y"]),
        }

    raise RuntimeError(
        f"'{query}' 장소를 찾지 못했어요. 건물명, 역 이름 또는 도로명 주소를 더 정확하게 입력해 주세요."
    )


def get_transit_routes(origin, destination, api_key):
    """카카오 대중교통 경로 API에서 경로와 예상 소요시간을 가져온다."""
    data = kakao_get(
        KAKAO_TRANSIT_URL,
        {
            "start_x": origin["x"],
            "start_y": origin["y"],
            "s_name": origin["name"],
            "end_x": destination["x"],
            "end_y": destination["y"],
            "e_name": destination["name"],
            "input_coord": "WGS84",
            "output_coord": "WGS84",
        },
        api_key,
    )

    status = data.get("status")
    if status != "OK":
        messages = {
            "STARTNODES_NULL": "출발지 주변에서 대중교통 승차 지점을 찾지 못했어요.",
            "ENDNODES_NULL": "목적지 주변에서 대중교통 하차 지점을 찾지 못했어요.",
            "EQUAL_POINTS": "출발지와 목적지가 같은 위치로 인식됐어요.",
            "INVALID_REQUEST": "경로 요청 정보가 올바르지 않아요. 장소를 다시 입력해 주세요.",
            "NO_RESULTS": "해당 출발지와 목적지 사이의 대중교통 경로를 찾지 못했어요.",
        }
        raise RuntimeError(messages.get(status, f"대중교통 경로 조회에 실패했어요. (상태: {status})"))

    routes = []
    for raw in data.get("routes", []):
        props = raw.get("properties", {}) or {}
        try:
            total_seconds = int(props.get("totalTime", 0))
        except (TypeError, ValueError):
            continue
        if total_seconds <= 0:
            continue

        steps = []
        for step in raw.get("steps", []) or []:
            step_props = step.get("properties", {}) or {}
            guidance = step_props.get("guidance")
            step_type = step_props.get("type", "")
            if guidance:
                steps.append({"type": step_type, "guidance": guidance})

        fare_data = props.get("fare", {}) or {}
        routes.append(
            {
                "type": props.get("type", "대중교통"),
                "seconds": total_seconds,
                "minutes": max(1, round(total_seconds / 60)),
                "transfers": int(props.get("transfers", 0) or 0),
                "distance": int(props.get("totalDistance", 0) or 0),
                "fare": fare_data.get("value"),
                "steps": steps,
                "landing_url": data.get("properties", {}).get("landingURL", ""),
            }
        )

    if not routes:
        raise RuntimeError("경로 정보는 받았지만 예상 소요시간을 확인하지 못했어요.")

    routes.sort(key=lambda route: route["seconds"])
    return routes


def calculate_departure(arrival_datetime, travel_minutes, buffer_minutes):
    departure = arrival_datetime - timedelta(minutes=travel_minutes + buffer_minutes)
    now = datetime.now(KST)
    return departure, now


def route_type_label(route_type):
    return {
        "BUS": "버스",
        "SUBWAY": "지하철",
        "BUS_AND_SUBWAY": "버스 + 지하철",
    }.get(route_type, route_type or "대중교통")


# ------------------------------------------------------------
# 화면
# ------------------------------------------------------------
st.markdown(
    """
    <div class="hero">
      <h1>🚌 몇 시에 나갈까?</h1>
      <p>출발지와 목적지, 도착해야 하는 시각을 입력하면 집에서 나갈 시간을 계산해 드려요.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

with st.sidebar:
    st.subheader("API 설정")
    api_key = get_api_key()
    entered_key = st.text_input(
        "카카오 REST API 키",
        type="password",
        placeholder="REST API 키 입력",
        help="Streamlit Cloud에서는 Settings → Secrets에 KAKAO_REST_API_KEY로 저장하는 방법을 권장합니다.",
    )
    if entered_key.strip():
        api_key = entered_key.strip()
    if api_key:
        st.success("API 키가 설정되어 있어요.")
    else:
        st.warning("장소 검색과 대중교통 길찾기를 사용하려면 API 키가 필요해요.")
    st.markdown("[카카오 디벨로퍼스에서 앱 및 REST API 키 확인](https://developers.kakao.com/)")
    st.divider()
    st.caption("이 앱은 장소 검색과 대중교통 경로 조회에 카카오맵 API를 사용합니다.")

now = datetime.now(KST)
with st.form("trip_form"):
    st.markdown("### 어디에서 어디로 가나요?")
    col1, col2 = st.columns(2)
    with col1:
        origin_text = st.text_input(
            "출발지",
            placeholder="예: 수원역, 집 주소, 학교 이름",
        )
    with col2:
        destination_text = st.text_input(
            "목적지",
            placeholder="예: 강남역, 회사 이름, 건물 주소",
        )

    st.markdown("### 몇 시까지 도착해야 하나요?")
    date_col, time_col = st.columns(2)
    with date_col:
        arrival_date = st.date_input("도착 날짜", value=now.date(), min_value=now.date())
    with time_col:
        default_arrival_time = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0).time()
        arrival_time = st.time_input("도착 희망 시각", value=default_arrival_time)

    buffer_minutes = st.slider(
        "여유 있게 도착하기 위한 추가 시간",
        min_value=0,
        max_value=40,
        value=10,
        step=5,
        help="경로 API 예상시간에 더해 둘 여유 시간입니다. 환승 대기나 혼잡 등을 고려해 설정하세요.",
    )

    submitted = st.form_submit_button("🚀 출발 시간 계산하기", type="primary", use_container_width=True)

if submitted:
    if not api_key:
        st.error("카카오 REST API 키를 먼저 설정해 주세요. 왼쪽 사이드바에 입력하거나 Streamlit Cloud Secrets에 등록하면 됩니다.")
    elif not origin_text.strip() or not destination_text.strip():
        st.warning("출발지와 목적지를 모두 입력해 주세요.")
    else:
        arrival_datetime = datetime.combine(arrival_date, arrival_time).replace(tzinfo=KST)
        if arrival_datetime <= datetime.now(KST):
            st.error("도착 희망 시각은 현재 시각보다 뒤로 설정해 주세요.")
        else:
            try:
                with st.spinner("출발지와 목적지를 찾고 대중교통 경로를 계산하고 있어요..."):
                    origin = search_place(origin_text.strip(), api_key)
                    destination = search_place(destination_text.strip(), api_key)
                    routes = get_transit_routes(origin, destination, api_key)

                fastest = routes[0]
                departure, current_time = calculate_departure(
                    arrival_datetime,
                    fastest["minutes"],
                    buffer_minutes,
                )

                st.divider()
                st.markdown("## 출발 시간 안내")
                st.write(f"**{origin['name']}**에서 **{destination['name']}**까지 이동하는 경로를 찾았어요.")

                st.markdown(
                    f"""
                    <div class="answer">
                      <div class="answer-label">추천 출발 시각 · 여유 시간 {buffer_minutes}분 포함</div>
                      <div class="answer-time">{departure.strftime('%Y년 %m월 %d일 %H:%M')}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

                metric1, metric2, metric3 = st.columns(3)
                metric1.metric("예상 이동 시간", f"약 {fastest['minutes']}분")
                metric2.metric("환승 횟수", f"{fastest['transfers']}회")
                metric3.metric("목표 도착", arrival_datetime.strftime("%H:%M"))

                if departure <= current_time:
                    st.warning("계산된 출발 시각이 이미 지났어요. 지금 바로 이동하더라도 목표 시각에 늦을 수 있으니 실제 운행 정보를 확인해 주세요.")
                else:
                    remaining = departure - current_time
                    remaining_minutes = max(0, int(remaining.total_seconds() // 60))
                    st.info(f"현재 시각 기준으로 약 **{remaining_minutes}분 뒤** 출발하면 됩니다.")

                with st.expander("출발지·목적지 인식 결과"):
                    st.write(f"- 출발지: {origin['name']} — {origin['address']}")
                    st.write(f"- 목적지: {destination['name']} — {destination['address']}")
                    st.caption("검색 결과가 의도한 장소와 다르면 역 이름, 지점명 또는 도로명 주소를 더 구체적으로 입력해 주세요.")

                st.markdown("### 다른 이동 경로 비교")
                for index, route in enumerate(routes[:5], start=1):
                    route_departure = arrival_datetime - timedelta(minutes=route["minutes"] + buffer_minutes)
                    title = f"경로 {index} · {route_type_label(route['type'])} · 약 {route['minutes']}분"
                    with st.expander(title, expanded=(index == 1)):
                        a, b, c = st.columns(3)
                        a.metric("추천 출발", route_departure.strftime("%H:%M"))
                        b.metric("환승", f"{route['transfers']}회")
                        c.metric("총 이동 거리", f"{route['distance']:,}m" if route["distance"] else "정보 없음")
                        if route["fare"] is not None:
                            st.write(f"예상 요금: 약 {int(route['fare']):,}원")
                        if route["steps"]:
                            st.write("**이동 순서**")
                            for step in route["steps"]:
                                st.write(f"- {step['guidance']}")

                if fastest.get("landing_url"):
                    st.link_button("카카오맵에서 경로 자세히 보기", fastest["landing_url"], use_container_width=True)

                st.caption(
                    "계산 방식: 카카오 대중교통 경로의 예상 소요시간 + 사용자가 설정한 여유 시간을 도착 희망 시각에서 뺍니다. "
                    "경로 시간은 실시간 교통 상황, 실제 출발 시각의 배차 간격, 환승 대기 등에 따라 달라질 수 있으므로 참고용으로 이용해 주세요."
                )
            except RuntimeError as exc:
                st.error(str(exc))
            except (KeyError, TypeError, ValueError) as exc:
                st.error(f"응답 데이터 처리 중 문제가 발생했어요. 장소를 다시 입력하거나 잠시 후 시도해 주세요. ({exc})")

st.divider()
st.caption("본 프로그램은 대중교통 경로를 바탕으로 대략적인 출발 시각을 계산합니다. 실제 운행·배차 상황을 보장하지 않으므로 중요한 일정에는 여유 시간을 충분히 두세요.")
