import math
from datetime import datetime, timedelta, time
from urllib.parse import unquote
from zoneinfo import ZoneInfo

import requests
import streamlit as st


# ------------------------------------------------------------
# 기본 설정
# ------------------------------------------------------------
st.set_page_config(
    page_title="버스 출발 타이밍 도우미",
    page_icon="🚌",
    layout="wide",
    initial_sidebar_state="expanded",
)

KST = ZoneInfo("Asia/Seoul")
BUS_STOP_URL = "https://apis.data.go.kr/1613000/BusSttnInfoInqireService/getCrdntPrxmtSttnList"
BUS_ARRIVAL_URL = "https://apis.data.go.kr/1613000/ArvlInfoInqireService/getSttnAcctoArvlPrearngeInfoList"

st.markdown(
    """
    <style>
    .block-container {max-width: 1120px; padding-top: 1.8rem; padding-bottom: 3rem;}
    .hero {
        padding: 1.5rem 1.6rem; border-radius: 18px;
        background: linear-gradient(120deg, #12334a 0%, #176b75 100%);
        color: white; margin-bottom: 1rem;
    }
    .hero h1 {font-size: 2rem; margin: 0 0 .45rem 0; color: white;}
    .hero p {margin: 0; opacity: .92; font-size: 1rem;}
    .small-note {font-size: .88rem; color: #687782;}
    div[data-testid="stMetric"] {background: rgba(120, 130, 140, .08); padding: .9rem; border-radius: 12px;}
    </style>
    """,
    unsafe_allow_html=True,
)


# ------------------------------------------------------------
# 공통 함수
# ------------------------------------------------------------
def get_secret_key():
    """Streamlit Cloud Secrets에 저장된 인증키를 읽는다."""
    try:
        return str(st.secrets.get("TAGO_SERVICE_KEY", "")).strip()
    except Exception:
        return ""


def normalize_service_key(key):
    """공공데이터포털에서 복사한 URL 인코딩 키도 처리한다."""
    return unquote(str(key or "").strip())


def get_value(item, *names, default=""):
    for name in names:
        value = item.get(name)
        if value is not None and value != "":
            return value
    return default


def response_items(data):
    """TAGO JSON 응답에서 item 목록을 꺼내고 API 오류를 구분한다."""
    if not isinstance(data, dict):
        raise RuntimeError("API 응답 형식이 예상과 달라요. 공공데이터포털의 응답 형식을 확인해 주세요.")

    # 공공데이터포털 게이트웨이가 반환하는 인증/서비스 오류 형식
    gateway_error = data.get("OpenAPI_ServiceResponse")
    if gateway_error:
        header = gateway_error.get("cmmMsgHeader", {}) if isinstance(gateway_error, dict) else {}
        message = get_value(header, "errMsg", "returnAuthMsg", "returnReasonCode", default="API 게이트웨이 오류")
        raise RuntimeError(f"공공데이터포털 API 오류: {message}. API 신청 상태와 인증키를 확인해 주세요.")

    root = data.get("response", data)
    if not isinstance(root, dict):
        raise RuntimeError("API 응답 형식이 올바르지 않아요.")

    header = root.get("header", {}) or {}
    code = str(header.get("resultCode", "00"))
    message = str(header.get("resultMsg", "정상 응답"))

    if code not in ("00", "0", "0000", "NORMAL_CODE"):
        if code in ("20", "30") or "SERVICE_KEY" in message.upper() or "인증" in message:
            raise RuntimeError("인증키 오류 또는 API 이용 권한 문제예요. 공공데이터포털에서 버스도착정보 API 활용신청과 인증키를 확인해 주세요.")
        raise RuntimeError(f"API 응답 오류({code}): {message}")

    body = root.get("body", {}) or {}
    if not isinstance(body, dict):
        return []
    items = body.get("items", {})
    if items is None:
        return []

    if isinstance(items, dict):
        found = items.get("item", [])
    else:
        found = items

    if found is None or found == "":
        return []
    if isinstance(found, list):
        return [row for row in found if isinstance(row, dict)]
    if isinstance(found, dict):
        return [found]
    return []


def call_tago_api(url, params, service_key):
    """TAGO API를 호출하고 JSON 응답을 반환한다."""
    query = dict(params)
    query["serviceKey"] = normalize_service_key(service_key)
    query["_type"] = "json"

    try:
        response = requests.get(
            url,
            params=query,
            timeout=15,
            headers={"User-Agent": "BusDepartureHelper/1.0"},
        )
        response.raise_for_status()
    except requests.Timeout:
        raise RuntimeError("API 응답 시간이 초과됐어요. 잠시 후 다시 시도해 주세요.")
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "알 수 없음"
        raise RuntimeError(f"API 서버가 HTTP {status} 오류를 반환했어요. API 주소와 이용 권한을 확인해 주세요.")
    except requests.RequestException:
        raise RuntimeError("API 서버에 연결하지 못했어요. 인터넷 연결이나 API 서버 상태를 확인해 주세요.")

    try:
        data = response.json()
    except ValueError:
        # 인증 오류 응답이 XML로 반환되는 경우에도 사용자가 원인을 파악할 수 있도록 한다.
        import xml.etree.ElementTree as ET
        try:
            root = ET.fromstring(response.text)
            values = {element.tag.split("}")[-1]: (element.text or "").strip() for element in root.iter()}
            msg = values.get("returnAuthMsg") or values.get("errMsg") or values.get("returnReasonCode") or values.get("resultMsg")
            if msg:
                raise RuntimeError(f"API 응답 오류: {msg}. 인증키와 API 활용신청 상태를 확인해 주세요.")
        except ET.ParseError:
            pass
        raise RuntimeError("API 응답을 JSON으로 읽지 못했어요. _type=json 요청과 인증키를 확인해 주세요.")

    return data


def fetch_nearby_stations(service_key, latitude, longitude):
    data = call_tago_api(
        BUS_STOP_URL,
        {
            "gpsLati": latitude,
            "gpsLong": longitude,
            "numOfRows": 50,
            "pageNo": 1,
        },
        service_key,
    )
    raw_items = response_items(data)
    stations = []

    for item in raw_items:
        node_id = str(get_value(item, "nodeid", "nodeId", "nodeID"))
        city_code = str(get_value(item, "citycode", "cityCode", "city_code"))
        name = str(get_value(item, "nodenm", "nodeNm", "stationName", default="이름 없는 정류장"))
        if not node_id or not city_code:
            continue

        lat = get_value(item, "gpslati", "gpsLati", default=None)
        lon = get_value(item, "gpslong", "gpsLong", default=None)
        distance = None
        try:
            if lat is not None and lon is not None:
                lat1, lon1 = math.radians(float(latitude)), math.radians(float(longitude))
                lat2, lon2 = math.radians(float(lat)), math.radians(float(lon))
                dlat, dlon = lat2 - lat1, lon2 - lon1
                a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
                distance = 6371000 * 2 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1 - a)))
        except (TypeError, ValueError):
            distance = None

        stations.append(
            {
                "name": name,
                "node_id": node_id,
                "city_code": city_code,
                "latitude": lat,
                "longitude": lon,
                "distance": distance,
            }
        )

    stations.sort(key=lambda station: station["distance"] if station["distance"] is not None else 999999)
    return stations


def fetch_arrivals(service_key, city_code, node_id):
    data = call_tago_api(
        BUS_ARRIVAL_URL,
        {
            "cityCode": str(city_code).strip(),
            "nodeId": str(node_id).strip(),
            "numOfRows": 30,
            "pageNo": 1,
        },
        service_key,
    )
    raw_items = response_items(data)
    arrivals = []

    for item in raw_items:
        route_no = str(get_value(item, "routeno", "routeNo", "route_no", default="노선 번호 미상"))
        route_type = str(get_value(item, "routetp", "routeTp", "routeType", default=""))
        raw_seconds = get_value(item, "arrtime", "arrTime", "arrivalTime", default=None)
        stops_left = get_value(item, "arrprevstationcnt", "arrPrevStationCnt", default=None)
        vehicle_type = str(get_value(item, "vehicletp", "vehicleTp", default=""))
        try:
            seconds = int(float(raw_seconds))
        except (TypeError, ValueError):
            continue
        if seconds < 0:
            continue

        try:
            stops_left = int(stops_left) if stops_left not in (None, "") else None
        except (TypeError, ValueError):
            stops_left = None

        arrivals.append(
            {
                "route_no": route_no,
                "route_type": route_type,
                "seconds": seconds,
                "stops_left": stops_left,
                "vehicle_type": vehicle_type,
                "source": "API",
            }
        )

    arrivals.sort(key=lambda item: item["seconds"])
    return arrivals


def format_minutes(value):
    return f"{max(0, int(math.ceil(value)))}분"


def get_now():
    return datetime.now(KST).replace(microsecond=0)


def make_deadline(target_clock, now):
    target = datetime.combine(now.date(), target_clock).replace(tzinfo=KST)
    return target


def recommend_departure(arrivals, target_clock, walk_minutes, boarding_buffer, ride_minutes, final_walk_minutes):
    """버스 도착 ETA와 사용자가 입력한 이동 시간을 비교해 출발 시점을 계산한다."""
    now = get_now()
    deadline = make_deadline(target_clock, now)
    if deadline <= now:
        return None, "목표 도착 시각이 현재 시각이거나 이미 지났어요. 오늘의 현재 시각 이후로 설정해 주세요."

    if not arrivals:
        return None, "조회된 버스 도착 정보가 없어요. 정류장이나 노선번호를 확인하거나 잠시 후 다시 조회해 주세요."

    deadline_minutes = (deadline - now).total_seconds() / 60
    required_before_bus = walk_minutes + boarding_buffer
    prepared = []

    for bus in arrivals:
        eta_minutes = bus["seconds"] / 60
        destination_minutes = eta_minutes + ride_minutes + final_walk_minutes
        can_catch = eta_minutes >= required_before_bus
        on_time = destination_minutes <= deadline_minutes
        prepared.append(
            {
                **bus,
                "eta_minutes": eta_minutes,
                "expected_destination_minutes": destination_minutes,
                "can_catch": can_catch,
                "on_time": on_time,
                "bus_arrival_clock": (now + timedelta(seconds=bus["seconds"])).strftime("%H:%M"),
                "destination_clock": (now + timedelta(minutes=destination_minutes)).strftime("%H:%M"),
            }
        )

    # 목표 시각에 맞출 수 있는 버스 중 가장 늦게 도착하는 버스를 선택한다.
    on_time_options = [bus for bus in prepared if bus["can_catch"] and bus["on_time"]]
    catchable_options = [bus for bus in prepared if bus["can_catch"]]

    if on_time_options:
        chosen = max(on_time_options, key=lambda bus: bus["eta_minutes"])
        status = "on_time"
    elif catchable_options:
        chosen = min(catchable_options, key=lambda bus: bus["eta_minutes"])
        status = "late"
    else:
        chosen = min(prepared, key=lambda bus: bus["eta_minutes"])
        status = "hard_to_catch"

    leave_after_minutes = chosen["eta_minutes"] - required_before_bus
    if leave_after_minutes >= 0:
        chosen["leave_after_minutes"] = leave_after_minutes
        chosen["leave_clock"] = (now + timedelta(minutes=leave_after_minutes)).strftime("%H:%M")
    else:
        chosen["leave_after_minutes"] = 0
        chosen["leave_clock"] = "지금 바로"

    chosen["status"] = status
    chosen["deadline_clock"] = deadline.strftime("%H:%M")
    chosen["minutes_late"] = max(0, chosen["expected_destination_minutes"] - deadline_minutes)
    chosen["all_buses"] = prepared
    return chosen, None


def show_recommendation(arrivals, target_clock, walk_minutes, boarding_buffer, ride_minutes, final_walk_minutes, mode_label):
    chosen, error = recommend_departure(
        arrivals,
        target_clock,
        walk_minutes,
        boarding_buffer,
        ride_minutes,
        final_walk_minutes,
    )
    if error:
        st.warning(error)
        return

    if chosen["status"] == "on_time":
        st.success("목표 도착 시각에 맞출 수 있는 버스를 찾았어요.")
    elif chosen["status"] == "late":
        st.warning("현재 조회된 버스로는 목표 시각보다 늦게 도착할 것으로 예상돼요.")
    else:
        st.error("가장 빠른 버스도 걷는 시간과 승차 여유 시간을 고려하면 놓칠 가능성이 있어요. 가능한 한 빨리 이동하거나 다음 버스를 확인하세요.")

    c1, c2, c3 = st.columns(3)
    c1.metric("추천 노선", chosen["route_no"])
    c2.metric("버스 도착 예상", chosen["bus_arrival_clock"], format_minutes(chosen["eta_minutes"]) + " 후")
    c3.metric("추천 출발", chosen["leave_clock"], "집/현재 위치에서 정류장까지 기준")

    st.markdown("#### 출발 타이밍 분석")
    left, right = st.columns(2)
    with left:
        st.write(f"- **정류장 도착 예상:** {chosen['bus_arrival_clock']}")
        st.write(f"- **목표 도착 시각:** {chosen['deadline_clock']}")
        st.write(f"- **목적지 도착 예상:** {chosen['destination_clock']}")
    with right:
        st.write(f"- **정류장까지 걷는 시간:** {walk_minutes}분")
        st.write(f"- **승차 여유 시간:** {boarding_buffer}분")
        st.write(f"- **버스 탑승 후 이동 시간:** {ride_minutes}분 + 마지막 도보 {final_walk_minutes}분")

    if chosen["leave_after_minutes"] > 0:
        st.info(f"지금부터 약 **{int(math.floor(chosen['leave_after_minutes']))}분 뒤**인 **{chosen['leave_clock']}**에 출발하는 것을 추천해요.")
    elif chosen["status"] == "on_time":
        st.info("이미 출발해도 되는 시점이에요. 버스 도착 상황이 변할 수 있으니 가능한 한 바로 이동하세요.")
    else:
        st.info("추천 출발 시각이 이미 지났을 수 있어요. 가능한 한 바로 이동하고, 다음 버스 정보도 확인하세요.")

    if chosen["status"] == "late":
        st.warning(f"입력한 이동 시간 기준으로 목표보다 약 {format_minutes(chosen['minutes_late'])} 늦을 수 있어요. 목표 시각을 조정하거나 다른 교통수단도 고려하세요.")

    table_rows = []
    for bus in sorted(chosen["all_buses"], key=lambda item: item["seconds"]):
        stops_text = "정보 없음" if bus["stops_left"] is None else f"{bus['stops_left']}개"
        table_rows.append(
            {
                "노선": bus["route_no"],
                "유형": bus["route_type"] or "-",
                "도착까지": format_minutes(bus["eta_minutes"]),
                "정류장 수": stops_text,
                "목적지 도착 예상": bus["destination_clock"],
                "목표 시간 내 도착": "가능" if bus["on_time"] and bus["can_catch"] else ("어려움" if not bus["on_time"] else "승차시간 부족"),
            }
        )

    st.markdown("#### 조회된 버스 비교")
    st.dataframe(table_rows, use_container_width=True, hide_index=True)
    st.caption(
        f"분석 방식: {mode_label}. API 도착예정시간과 사용자가 입력한 예상 이동 시간을 이용한 계산이며, 교통 정체·운행 변동에 따라 실제 도착 시각은 달라질 수 있어요."
    )


# ------------------------------------------------------------
# 화면 구성
# ------------------------------------------------------------
st.markdown(
    """
    <div class="hero">
      <h1>🚌 버스 출발 타이밍 도우미</h1>
      <p>버스 도착 정보와 내 이동 시간을 계산해, 언제 출발하면 좋을지 알려 줍니다.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

with st.sidebar:
    st.subheader("사용 방법")
    st.markdown(
        "1. 실시간 API 또는 샘플 모드를 선택합니다.\n"
        "2. 정류장과 이동 시간을 입력합니다.\n"
        "3. 목표 시각에 맞는 출발 타이밍을 확인합니다."
    )
    st.divider()
    st.markdown("**실시간 API 인증키**")
    secret_key = get_secret_key()
    typed_key = st.text_input(
        "공공데이터포털 서비스키",
        type="password",
        placeholder="인증키 붙여넣기 (선택)",
        help="앱에 키를 직접 넣는 대신 Streamlit Cloud의 Settings → Secrets에 TAGO_SERVICE_KEY로 저장하는 방법을 권장해요.",
    )
    api_key = typed_key.strip() or secret_key
    if api_key:
        st.success("인증키가 입력되었어요.")
    else:
        st.info("인증키가 없어도 샘플 모드를 사용할 수 있어요.")
    st.markdown("[버스 도착 API 신청](https://www.data.go.kr/data/15098530/openapi.do)")
    st.markdown("[주변 정류장 API 신청](https://www.data.go.kr/data/15096280/standard.do)")

mode = st.radio(
    "이용 모드",
    ["실시간 버스 데이터(API)", "샘플 데이터 체험"],
    horizontal=True,
    help="실시간 모드는 TAGO API 인증키가 필요합니다. 샘플 모드는 가상의 도착 시간으로 계산 기능을 시험합니다.",
)

if mode == "실시간 버스 데이터(API)":
    st.markdown("### 1. 정류장 선택")
    if not api_key:
        st.warning(
            "실시간 조회를 사용하려면 공공데이터포털에서 **국토교통부(TAGO) 버스도착정보**와 **버스정류소정보** API를 신청한 뒤 인증키를 입력해 주세요. 신청이 완료되기 전에는 API 조회가 되지 않을 수 있어요."
        )

    station_method = st.radio(
        "정류장 입력 방법",
        ["좌표로 주변 정류장 찾기", "정류소 코드 직접 입력"],
        index=1,
        horizontal=True,
        help="첨부한 화면의 버스도착정보 API만 신청한 경우에는 '정류소 코드 직접 입력'을 먼저 이용하세요. 주변 정류장 검색에는 별도의 TAGO 버스정류소정보 API 활용신청이 필요합니다.",
    )

    if station_method == "좌표로 주변 정류장 찾기":
        st.caption("위도·경도 기준 반경 약 500m의 정류장을 조회합니다. 이 기능은 첨부한 버스도착정보 API와 별도로 '국토교통부(TAGO) 버스정류소정보' API도 활용신청해야 할 수 있습니다. 기본 좌표는 서울시청 예시이며 변경할 수 있습니다.")
        with st.form("station_search_form"):
            col1, col2 = st.columns(2)
            with col1:
                latitude = st.number_input("위도", min_value=-90.0, max_value=90.0, value=37.5665, format="%.6f")
            with col2:
                longitude = st.number_input("경도", min_value=-180.0, max_value=180.0, value=126.9780, format="%.6f")
            find_stations = st.form_submit_button("주변 정류장 찾기", type="primary", use_container_width=True)

        if find_stations:
            if not api_key:
                st.session_state["stations"] = []
                st.session_state["station_search_error"] = "먼저 공공데이터포털 인증키를 입력해 주세요."
            else:
                try:
                    with st.spinner("주변 정류장 정보를 조회하고 있어요..."):
                        st.session_state["stations"] = fetch_nearby_stations(api_key, latitude, longitude)
                    st.session_state["station_search_error"] = ""
                    st.session_state["station_coords"] = (latitude, longitude)
                except (RuntimeError, ValueError) as exc:
                    st.session_state["stations"] = []
                    st.session_state["station_search_error"] = str(exc)

        search_error = st.session_state.get("station_search_error", "")
        if search_error:
            st.error(search_error)

        stations = st.session_state.get("stations", [])
        if stations:
            st.markdown("#### 주변 정류장 검색 결과")
            labels = []
            station_by_label = {}
            for station in stations:
                dist = f" · 약 {round(station['distance'])}m" if station["distance"] is not None else ""
                label = f"{station['name']} · 도시코드 {station['city_code']} · ID {station['node_id']}{dist}"
                labels.append(label)
                station_by_label[label] = station

            selected_label = st.selectbox("이용할 정류장 선택", labels)
            selected_station = station_by_label[selected_label]
            st.caption(f"선택한 정류장: {selected_station['name']} (정류장 ID: {selected_station['node_id']})")

            st.markdown("### 2. 이동 조건 입력")
            with st.form("live_arrival_form"):
                col1, col2 = st.columns(2)
                with col1:
                    route_filter = st.text_input("이용할 버스 노선번호 (선택)", placeholder="예: 10, 300 또는 비워 두기")
                    target_clock = st.time_input("목표 도착 시각", value=(get_now() + timedelta(minutes=60)).time().replace(second=0, microsecond=0))
                    walk_minutes = st.number_input("현재 위치에서 정류장까지 걷는 시간 (분)", min_value=0, max_value=120, value=5)
                    boarding_buffer = st.number_input("승차 여유 시간 (분)", min_value=0, max_value=30, value=3)
                with col2:
                    ride_minutes = st.number_input("버스 탑승 후 목적지까지 예상 이동 시간 (분)", min_value=0, max_value=240, value=25)
                    final_walk_minutes = st.number_input("하차 후 목적지까지 걷는 시간 (분)", min_value=0, max_value=120, value=5)
                    st.caption("버스 탑승 후 이동 시간은 노선과 목적지에 따라 직접 예상해서 입력하세요.")
                analyze_live = st.form_submit_button("실시간 도착정보 조회 및 분석", type="primary", use_container_width=True)

            if analyze_live:
                if not api_key:
                    st.error("인증키를 입력해 주세요.")
                else:
                    try:
                        with st.spinner("실시간 버스 도착정보를 확인하고 있어요..."):
                            arrivals = fetch_arrivals(api_key, selected_station["city_code"], selected_station["node_id"])
                        if route_filter.strip():
                            exact_filter = route_filter.strip().replace(" ", "").lower()
                            arrivals = [
                                bus for bus in arrivals
                                if exact_filter in bus["route_no"].replace(" ", "").lower()
                            ]
                        if not arrivals:
                            if route_filter.strip():
                                st.warning("해당 정류장에서 입력한 노선번호의 도착정보를 찾지 못했어요. 노선번호를 확인하거나 입력란을 비워 두고 다시 조회해 보세요.")
                            else:
                                st.warning("현재 조회된 도착정보가 없어요. 운행하지 않는 시간대이거나 해당 정류장의 정보가 제공되지 않을 수 있어요.")
                        else:
                            show_recommendation(
                                arrivals,
                                target_clock,
                                int(walk_minutes),
                                int(boarding_buffer),
                                int(ride_minutes),
                                int(final_walk_minutes),
                                "실시간 TAGO API 데이터",
                            )
                            if not route_filter.strip():
                                st.caption("노선번호를 입력하지 않아, 조회된 모든 노선 중 계산상 가장 적합한 버스를 골랐어요. 실제 목적지로 가는 노선인지 반드시 확인하세요.")
                    except (RuntimeError, ValueError) as exc:
                        st.error(str(exc))
        else:
            st.info("먼저 위도와 경도를 확인한 뒤 **주변 정류장 찾기**를 눌러 주세요.")

    else:
        st.caption("첨부한 화면의 API(ArvlInfoInqireService)를 직접 호출하는 방식입니다. 도시코드(cityCode)와 정류장 ID(nodeId)가 필요해요. 아래 예시는 공식 문서에 나온 예시 값이며, 실제 이용 정류장 값과 다를 수 있습니다.")
        with st.form("direct_arrival_form"):
            col1, col2 = st.columns(2)
            with col1:
                city_code = st.text_input("도시코드 (cityCode)", placeholder="예: 25")
                node_id = st.text_input("정류장 ID (nodeId)", placeholder="예: DJB8001793")
                route_filter = st.text_input("이용할 버스 노선번호 (선택)", placeholder="예: 5")
                target_clock = st.time_input("목표 도착 시각", value=(get_now() + timedelta(minutes=60)).time().replace(second=0, microsecond=0))
                walk_minutes = st.number_input("정류장까지 걷는 시간 (분)", min_value=0, max_value=120, value=5, key="direct_walk")
                boarding_buffer = st.number_input("승차 여유 시간 (분)", min_value=0, max_value=30, value=3, key="direct_buffer")
            with col2:
                ride_minutes = st.number_input("버스 탑승 후 목적지까지 예상 이동 시간 (분)", min_value=0, max_value=240, value=25, key="direct_ride")
                final_walk_minutes = st.number_input("하차 후 목적지까지 걷는 시간 (분)", min_value=0, max_value=120, value=5, key="direct_final_walk")
                st.markdown(" ")
            analyze_direct = st.form_submit_button("실시간 도착정보 조회 및 분석", type="primary", use_container_width=True)

        if analyze_direct:
            if not api_key:
                st.error("인증키를 입력해 주세요.")
            elif not city_code.strip() or not node_id.strip():
                st.error("도시코드와 정류장 ID를 모두 입력해 주세요.")
            else:
                try:
                    with st.spinner("실시간 버스 도착정보를 확인하고 있어요..."):
                        arrivals = fetch_arrivals(api_key, city_code.strip(), node_id.strip())
                    if route_filter.strip():
                        exact_filter = route_filter.strip().replace(" ", "").lower()
                        arrivals = [bus for bus in arrivals if exact_filter in bus["route_no"].replace(" ", "").lower()]
                    if not arrivals:
                        st.warning("조건에 맞는 도착정보가 없어요. 입력한 정류장과 노선번호를 확인해 주세요.")
                    else:
                        show_recommendation(
                            arrivals,
                            target_clock,
                            int(walk_minutes),
                            int(boarding_buffer),
                            int(ride_minutes),
                            int(final_walk_minutes),
                            "실시간 TAGO API 데이터",
                        )
                        if not route_filter.strip():
                            st.caption("노선번호를 입력하지 않아 모든 조회 노선을 비교했어요. 실제 목적지로 가는 노선인지 확인하세요.")
                except (RuntimeError, ValueError) as exc:
                    st.error(str(exc))

else:
    st.markdown("### 샘플 데이터로 체험하기")
    st.info("아래 버스 도착 시간은 **가상 예시**입니다. 실시간 교통정보가 아니며, 출발 타이밍 계산 기능을 시험하기 위한 데이터예요.")
    with st.form("sample_analysis_form"):
        st.markdown("#### 예시 버스 도착 시간")
        c1, c2, c3 = st.columns(3)
        with c1:
            route1 = st.text_input("노선 1", value="10")
            eta1 = st.number_input("노선 1 도착까지 (분)", min_value=0, max_value=180, value=8)
        with c2:
            route2 = st.text_input("노선 2", value="13")
            eta2 = st.number_input("노선 2 도착까지 (분)", min_value=0, max_value=180, value=16)
        with c3:
            route3 = st.text_input("노선 3", value="88")
            eta3 = st.number_input("노선 3 도착까지 (분)", min_value=0, max_value=180, value=27)

        st.markdown("#### 내 일정과 이동 시간")
        col1, col2 = st.columns(2)
        with col1:
            target_clock = st.time_input("목표 도착 시각", value=(get_now() + timedelta(minutes=60)).time().replace(second=0, microsecond=0), key="sample_target")
            walk_minutes = st.number_input("현재 위치에서 정류장까지 걷는 시간 (분)", min_value=0, max_value=120, value=5, key="sample_walk")
            boarding_buffer = st.number_input("승차 여유 시간 (분)", min_value=0, max_value=30, value=3, key="sample_buffer")
        with col2:
            ride_minutes = st.number_input("버스 탑승 후 목적지까지 예상 이동 시간 (분)", min_value=0, max_value=240, value=25, key="sample_ride")
            final_walk_minutes = st.number_input("하차 후 목적지까지 걷는 시간 (분)", min_value=0, max_value=120, value=5, key="sample_final_walk")
        run_sample = st.form_submit_button("출발 타이밍 계산", type="primary", use_container_width=True)

    if run_sample:
        sample_arrivals = []
        for route, eta in [(route1.strip() or "노선 1", eta1), (route2.strip() or "노선 2", eta2), (route3.strip() or "노선 3", eta3)]:
            sample_arrivals.append(
                {
                    "route_no": route,
                    "route_type": "샘플",
                    "seconds": int(eta) * 60,
                    "stops_left": None,
                    "vehicle_type": "",
                    "source": "샘플",
                }
            )
        sample_arrivals.sort(key=lambda item: item["seconds"])
        show_recommendation(
            sample_arrivals,
            target_clock,
            int(walk_minutes),
            int(boarding_buffer),
            int(ride_minutes),
            int(final_walk_minutes),
            "가상 샘플 데이터",
        )

st.divider()
st.caption(
    "참고: 실시간 모드의 버스 도착예정정보는 국토교통부 TAGO API에서 가져옵니다. API에서 제공하지 않는 실제 목적지까지의 이동 시간은 사용자가 직접 예상해 입력합니다. "
    "결과는 교통 상황에 따라 달라질 수 있으므로 공식 버스정보 서비스도 함께 확인하세요."
)
