from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import json

import requests
import streamlit as st
import streamlit.components.v1 as components


# ------------------------------------------------------------
# 앱 기본 설정
# ------------------------------------------------------------
st.set_page_config(
    page_title="몇 시에 나갈까?",
    page_icon="🚌",
    layout="centered",
    initial_sidebar_state="collapsed",
)

KST = ZoneInfo("Asia/Seoul")
KAKAO_LOCAL_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"
KAKAO_ADDRESS_URL = "https://dapi.kakao.com/v2/local/search/address.json"
KAKAO_TRANSIT_URL = "https://dapi.kakao.com/v2/routing/publictraffic"

st.markdown(
    """
    <style>
    [data-testid="stSidebar"],
    [data-testid="stSidebarCollapsedControl"],
    [data-testid="collapsedControl"] {
        display: none !important;
    }
    .block-container {
        max-width: 850px;
        padding-top: 2rem;
        padding-bottom: 3rem;
    }
    .hero {
        padding: 1.5rem;
        border-radius: 18px;
        background: linear-gradient(120deg, #153c59, #167c80);
        color: white;
        margin-bottom: 1.2rem;
    }
    .hero h1 {
        color: white;
        margin: 0 0 .45rem 0;
        font-size: 2rem;
    }
    .hero p {
        color: white;
        opacity: .92;
        margin: 0;
    }
    .answer {
        padding: 1.3rem;
        border: 1px solid rgba(22,124,128,.35);
        border-radius: 16px;
        background: rgba(22,124,128,.07);
    }
    .answer-label {
        font-size: .95rem;
        opacity: .8;
        margin-bottom: .4rem;
    }
    .answer-time {
        font-size: 2.1rem;
        font-weight: 800;
        line-height: 1.25;
    }
    .muted {
        font-size: .88rem;
        color: #687782;
    }
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
        raise RuntimeError(
            "카카오 API 사용 권한이 없어요. 카카오 디벨로퍼스에서 앱과 카카오맵 API 활성화 상태를 확인해 주세요."
        )
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
    """카카오 대중교통 경로와 단계별 노선·정류장 정보를 가져온다."""
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
    for raw_route in data.get("routes", []):
        props = raw_route.get("properties", {}) or {}
        try:
            total_seconds = int(props.get("totalTime", 0))
        except (TypeError, ValueError):
            continue
        if total_seconds <= 0:
            continue

        steps = []
        for raw_step in raw_route.get("steps", []) or []:
            step_props = raw_step.get("properties", {}) or {}

            vehicles = []
            for vehicle in step_props.get("vehicles", []) or []:
                vehicles.append(
                    {
                        "name": str(vehicle.get("name", "")).strip(),
                        "type": str(vehicle.get("type", "")).strip(),
                    }
                )

            stops = [
                str(stop.get("name", "")).strip()
                for stop in (step_props.get("stops", []) or [])
                if stop.get("name")
            ]

            # API 좌표는 [경도, 위도]이므로 Leaflet용 [위도, 경도]로 바꾼다.
            path_data = raw_step.get("path", {}) or {}
            map_points = []
            for coordinate in (path_data.get("points", []) or []):
                if not isinstance(coordinate, (list, tuple)) or len(coordinate) < 2:
                    continue
                try:
                    longitude = float(coordinate[0])
                    latitude = float(coordinate[1])
                except (TypeError, ValueError):
                    continue
                if -180 <= longitude <= 180 and -90 <= latitude <= 90:
                    map_points.append([latitude, longitude])

            try:
                step_time = int(step_props.get("time", 0) or 0)
            except (TypeError, ValueError):
                step_time = 0
            try:
                step_distance = int(step_props.get("distance", 0) or 0)
            except (TypeError, ValueError):
                step_distance = 0

            steps.append(
                {
                    "type": str(step_props.get("type", "")).upper(),
                    "guidance": str(step_props.get("guidance", "")).strip(),
                    "vehicles": vehicles,
                    "stops": stops,
                    "time": step_time,
                    "distance": step_distance,
                    "map_points": map_points,
                }
            )

        fare_data = props.get("fare", {}) or {}
        try:
            transfers = int(props.get("transfers", 0) or 0)
        except (TypeError, ValueError):
            transfers = 0

        routes.append(
            {
                "type": props.get("type", "대중교통"),
                "seconds": total_seconds,
                "minutes": max(1, round(total_seconds / 60)),
                "transfers": transfers,
                "distance": int(props.get("totalDistance", 0) or 0),
                "fare": fare_data.get("value"),
                "steps": steps,
                "landing_url": (data.get("properties", {}) or {}).get("landingURL", ""),
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


def vehicle_label(vehicle, step_type):
    """API가 돌려준 차량 종류와 노선명을 보기 쉬운 형태로 바꾼다."""
    name = vehicle.get("name", "").strip()
    category = vehicle.get("type", "").strip()
    if not name and not category:
        return "노선 정보 없음"

    if step_type == "BUS":
        bus_kind = category if category else ""
        if bus_kind and not bus_kind.endswith("버스"):
            bus_kind += "버스"
        number = name
        if number and not number.endswith("번"):
            number += "번"
        return " ".join(part for part in [bus_kind, number] if part).strip() or "버스 노선"

    return " ".join(part for part in [category, name] if part).strip() or "지하철 노선"


def render_route_steps(route):
    """걷기·버스·지하철을 순서대로 보여주고 각 노선의 승하차 정류장을 표시한다."""
    if not route.get("steps"):
        st.info("세부 이동 순서 정보가 제공되지 않았어요. 카카오맵에서 전체 경로를 확인해 주세요.")
        return

    bus_count = 0
    transit_seen = False

    for index, step in enumerate(route["steps"], start=1):
        step_type = step.get("type", "")
        if step_type == "BUS":
            bus_count += 1
            title_prefix = "첫 버스" if bus_count == 1 else f"환승 버스 {bus_count}번째"
            title = title_prefix
            transit_seen = True
        elif step_type == "SUBWAY":
            title = "지하철 이용" if not transit_seen else "환승 후 지하철 이용"
            transit_seen = True
        elif step_type == "WALKING":
            title = "도보 이동"
        else:
            title = "이동 구간"

        st.markdown(f"**{index}. {title}**")

        vehicles = step.get("vehicles", [])
        if vehicles:
            labels = [vehicle_label(vehicle, step_type) for vehicle in vehicles]
            st.write("이용 노선: " + " / ".join(labels))

        stops = step.get("stops", [])
        if stops and step_type in ("BUS", "SUBWAY"):
            st.write(f"탑승: **{stops[0]}**  →  하차: **{stops[-1]}**")
            if len(stops) > 2:
                with st.expander(f"경유 정류장·역 {len(stops)}곳 확인"):
                    st.write(" → ".join(stops))
        elif stops:
            st.write("경유 지점: " + " → ".join(stops))

        guidance = step.get("guidance", "")
        if guidance:
            st.caption(guidance)

        details = []
        if step.get("time", 0) > 0:
            details.append(f"약 {max(1, round(step['time'] / 60))}분")
        if step.get("distance", 0) > 0:
            details.append(f"약 {step['distance']:,}m")
        if details:
            st.caption(" · ".join(details))

        if index < len(route["steps"]):
            st.divider()


def render_route_comparison_map(origin, destination, routes):
    """추천 경로와 대안 경로의 API 좌표를 한 지도에 표시한다."""
    map_routes = []
    for index, route in enumerate(routes[:5], start=1):
        paths = []
        segments = []
        transit_order = 0
        for step in route.get("steps", []):
            points = step.get("map_points", [])
            if len(points) < 2:
                continue
            paths.append(points)

            step_type = step.get("type", "")
            if step_type in ("BUS", "SUBWAY"):
                transit_order += 1
                vehicle_names = [
                    vehicle_label(vehicle, step_type)
                    for vehicle in step.get("vehicles", [])
                ]
                stops = step.get("stops", [])
                mode_name = "버스" if step_type == "BUS" else "지하철"
                segments.append(
                    {
                        "order": transit_order,
                        "mode": mode_name,
                        "vehicle": " / ".join(vehicle_names) if vehicle_names else f"{mode_name} 노선 정보 없음",
                        "from_stop": stops[0] if stops else "승차 지점 정보 없음",
                        "to_stop": stops[-1] if stops else "하차 지점 정보 없음",
                        "start": points[0],
                        "end": points[-1],
                    }
                )

        if not paths:
            continue
        label = (
            f"경로 {index} · {route_type_label(route['type'])} · "
            f"약 {route['minutes']}분 · 환승 {route['transfers']}회"
        )
        map_routes.append(
            {
                "label": label,
                "paths": paths,
                "segments": segments,
                "recommended": index == 1,
            }
        )

    if not map_routes:
        st.info(
            "이 경로 응답에는 지도에 그릴 좌표가 포함되지 않았어요. "
            "아래 카카오맵 길찾기 버튼에서 전체 경로를 확인해 주세요."
        )
        return

    map_data = {
        "origin": [origin["y"], origin["x"]],
        "destination": [destination["y"], destination["x"]],
        "origin_name": origin["name"],
        "destination_name": destination["name"],
        "routes": map_routes,
    }
    map_json = json.dumps(map_data, ensure_ascii=False).replace("</", "<\\/")

    map_html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <style>
    html, body {{ margin: 0; padding: 0; font-family: sans-serif; }}
    #route-map {{ width: 100%; height: 510px; border-radius: 12px; }}
    .map-legend {{ display: flex; flex-wrap: wrap; gap: 8px 16px; margin: 10px 2px 0; font-size: 13px; }}
    .legend-item {{ display: inline-flex; align-items: center; gap: 6px; }}
    .legend-line {{ display: inline-block; width: 22px; height: 5px; border-radius: 4px; }}
    .map-note {{ color: #687782; font-size: 12px; margin: 8px 2px 0; }}
  </style>
</head>
<body>
  <div id="route-map" role="img" aria-label="추천 대중교통 경로와 대안 경로 지도"></div>
  <div id="route-legend" class="map-legend"></div>
  <div class="map-note">지도 선은 경로 API가 반환한 좌표를 연결한 것이며, 실제 운행 상황과 차이가 있을 수 있습니다.</div>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    const data = {map_json};
    const colors = ['#e4572e', '#2474d2', '#7b5ac7', '#26966f', '#bd4f91'];
    const map = L.map('route-map', {{ scrollWheelZoom: false }});
    L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
    }}).addTo(map);

    const allCoordinates = [];
    data.routes.forEach((route, routeIndex) => {{
      const color = colors[routeIndex % colors.length];
      route.paths.forEach((path) => {{
        if (path.length >= 2) {{
          L.polyline(path, {{
            color: color,
            weight: route.recommended ? 7 : 5,
            opacity: route.recommended ? 0.95 : 0.72,
            lineCap: 'round',
            lineJoin: 'round'
          }}).addTo(map).bindPopup(route.label);
          path.forEach(point => allCoordinates.push(point));
        }}
      }});

      // 추천 경로의 버스·지하철 탑승 순서를 번호로 표시한다.
      if (routeIndex === 0) {{
        (route.segments || []).forEach((segment) => {{
          const stepIcon = L.divIcon({{
            className: '',
            html: '<div style="width:28px;height:28px;line-height:28px;text-align:center;background:#fff;color:#172b4d;border:3px solid ' + color + ';border-radius:50%;font-weight:800;box-shadow:0 1px 5px #777">' + segment.order + '</div>',
            iconSize: [34, 34],
            iconAnchor: [17, 17]
          }});
          const popupText = segment.order + '번째 ' + segment.mode + ' 탑승<br>' +
            segment.vehicle + '<br>탑승: ' + segment.from_stop + '<br>하차: ' + segment.to_stop;
          L.marker(segment.start, {{ icon: stepIcon }}).addTo(map).bindPopup(popupText);
          L.circleMarker(segment.end, {{ radius: 5, color: color, fillColor: '#fff', fillOpacity: 1, weight: 3 }})
            .addTo(map).bindPopup(segment.order + '번째 구간 하차: ' + segment.to_stop);
          allCoordinates.push(segment.start, segment.end);
        }});
      }}

      const legend = document.createElement('div');
      legend.className = 'legend-item';
      const line = document.createElement('span');
      line.className = 'legend-line';
      line.style.backgroundColor = color;
      const label = document.createElement('span');
      label.textContent = route.label + (route.recommended ? ' (추천)' : '');
      legend.appendChild(line);
      legend.appendChild(label);
      document.getElementById('route-legend').appendChild(legend);
    }});

    const startIcon = L.divIcon({{
      className: '',
      html: '<div style="background:#167c80;color:#fff;border:2px solid white;border-radius:18px;padding:5px 8px;font-weight:700;white-space:nowrap;box-shadow:0 1px 5px #777">출발</div>',
      iconSize: [48, 28], iconAnchor: [24, 14]
    }});
    const endIcon = L.divIcon({{
      className: '',
      html: '<div style="background:#263b53;color:#fff;border:2px solid white;border-radius:18px;padding:5px 8px;font-weight:700;white-space:nowrap;box-shadow:0 1px 5px #777">도착</div>',
      iconSize: [48, 28], iconAnchor: [24, 14]
    }});
    L.marker(data.origin, {{icon: startIcon}}).addTo(map).bindPopup('출발: ' + data.origin_name);
    L.marker(data.destination, {{icon: endIcon}}).addTo(map).bindPopup('도착: ' + data.destination_name);
    allCoordinates.push(data.origin, data.destination);
    map.fitBounds(allCoordinates, {{ padding: [28, 28] }});
  </script>
</body>
</html>"""
    components.html(map_html, height=585, scrolling=False)


# ------------------------------------------------------------
# 화면
# ------------------------------------------------------------
st.markdown(
    """
    <div class="hero">
      <h1>🚌 몇 시에 나갈까?</h1>
      <p>출발지와 목적지, 도착해야 하는 시각을 입력하면 버스와 환승 경로 및 출발 시간을 알려드려요.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

api_key = get_api_key()
if not api_key:
    st.warning(
        "카카오 API 키가 설정되지 않았어요. Streamlit Cloud의 Settings → Secrets에 "
        '`KAKAO_REST_API_KEY = "발급받은_REST_API_키"` 형태로 등록해 주세요.'
    )

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
        default_arrival_time = (now + timedelta(hours=1)).replace(
            minute=0, second=0, microsecond=0
        ).time()
        arrival_time = st.time_input("도착 희망 시각", value=default_arrival_time)

    buffer_minutes = st.slider(
        "여유 있게 도착하기 위한 추가 시간",
        min_value=0,
        max_value=40,
        value=10,
        step=5,
        help="예상 이동 시간에 더해 둘 여유 시간입니다. 환승 대기나 혼잡 등을 고려해 설정하세요.",
    )

    submitted = st.form_submit_button(
        "🚀 출발 시간과 대중교통 경로 확인하기",
        type="primary",
        use_container_width=True,
    )

if submitted:
    if not api_key:
        st.error("카카오 REST API 키를 Streamlit Cloud Secrets에 먼저 등록해 주세요.")
    elif not origin_text.strip() or not destination_text.strip():
        st.warning("출발지와 목적지를 모두 입력해 주세요.")
    else:
        arrival_datetime = datetime.combine(arrival_date, arrival_time).replace(tzinfo=KST)
        if arrival_datetime <= datetime.now(KST):
            st.error("도착 희망 시각은 현재 시각보다 뒤로 설정해 주세요.")
        else:
            try:
                with st.spinner("출발지와 목적지를 찾고 버스·환승 경로를 계산하고 있어요..."):
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
                    st.warning(
                        "계산된 출발 시각이 이미 지났어요. 지금 바로 이동하더라도 목표 시각에 늦을 수 있으니 실제 운행 정보를 확인해 주세요."
                    )
                else:
                    remaining = departure - current_time
                    remaining_minutes = max(0, int(remaining.total_seconds() // 60))
                    st.info(f"현재 시각 기준으로 약 **{remaining_minutes}분 뒤** 출발하면 됩니다.")

                with st.expander("출발지·목적지 인식 결과"):
                    st.write(f"- 출발지: {origin['name']} — {origin['address']}")
                    st.write(f"- 목적지: {destination['name']} — {destination['address']}")
                    st.caption(
                        "검색 결과가 의도한 장소와 다르면 역 이름, 지점명 또는 도로명 주소를 더 구체적으로 입력해 주세요."
                    )

                st.markdown("### 지도에서 추천 경로와 다른 경로 비교")
                st.caption(
                    "추천 경로는 주황색 굵은 선, 대안 경로는 서로 다른 색의 선으로 표시했어요. "
                    "추천 경로 위의 번호 표시는 버스·지하철 탑승 순서이며, 선을 누르면 경로 정보를 볼 수 있어요."
                )
                render_route_comparison_map(origin, destination, routes)

                st.markdown("### 추천 경로의 버스·환승 순서")
                st.caption(
                    f"{route_type_label(fastest['type'])} · 환승 {fastest['transfers']}회 · "
                    f"총 이동 거리 {fastest['distance']:,}m"
                    if fastest["distance"]
                    else f"{route_type_label(fastest['type'])} · 환승 {fastest['transfers']}회"
                )
                render_route_steps(fastest)

                if fastest.get("fare") is not None:
                    st.write(f"예상 요금: 약 {int(fastest['fare']):,}원")

                st.markdown("### 다른 이동 경로 비교")
                if len(routes) > 1:
                    for index, route in enumerate(routes[1:5], start=2):
                        route_departure = arrival_datetime - timedelta(
                            minutes=route["minutes"] + buffer_minutes
                        )
                        title = (
                            f"경로 {index} · {route_type_label(route['type'])} · "
                            f"약 {route['minutes']}분 · 환승 {route['transfers']}회"
                        )
                        with st.expander(title):
                            a, b, c = st.columns(3)
                            a.metric("추천 출발", route_departure.strftime("%H:%M"))
                            b.metric("환승", f"{route['transfers']}회")
                            c.metric(
                                "총 이동 거리",
                                f"{route['distance']:,}m" if route["distance"] else "정보 없음",
                            )
                            render_route_steps(route)
                            if route.get("fare") is not None:
                                st.write(f"예상 요금: 약 {int(route['fare']):,}원")
                else:
                    st.info("비교할 수 있는 추가 경로가 없어요.")

                landing_url = fastest.get("landing_url")
                if landing_url:
                    st.link_button(
                        "카카오맵에서 경로 자세히 보기",
                        landing_url,
                        use_container_width=True,
                    )

                st.caption(
                    "계산 방식: 대중교통 경로의 예상 소요시간에 사용자가 설정한 여유 시간을 더한 뒤, "
                    "도착 희망 시각에서 뺍니다. 버스 번호와 정류장 정보는 경로 API가 반환한 내용을 표시하며, "
                    "실시간 도착 예정 시간이나 실제 배차를 보장하지는 않으므로 출발 전 운행 정보를 확인해 주세요."
                )
            except RuntimeError as exc:
                st.error(str(exc))
            except (KeyError, TypeError, ValueError) as exc:
                st.error(
                    "응답 데이터 처리 중 문제가 발생했어요. 장소를 다시 입력하거나 잠시 후 시도해 주세요. "
                    f"({exc})"
                )

st.divider()
st.caption(
    "본 프로그램은 대중교통 경로를 바탕으로 대략적인 출발 시각을 계산합니다. "
    "실제 운행·배차 상황을 보장하지 않으므로 중요한 일정에는 여유 시간을 충분히 두세요."
)
