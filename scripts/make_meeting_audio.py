import asyncio
import os
import edge_tts
from pydub import AudioSegment


# ============================================================
# Meeting Action Agent 데모용 회의 음성 생성기
#
# 필요 패키지:
#   python -m pip install edge-tts pydub audioop-lts
#
# 실행:
#   python .\scripts\make_meeting_audio.py
#
# 결과:
#   C:\work\과제\weekly_meeting_sample.mp3
# ============================================================


# ------------------------------------------------------------
# 1. 화자별 음성 설정
#
# 현재 확인된 한국어 Edge TTS 음성:
# - ko-KR-HyunsuMultilingualNeural
# - ko-KR-InJoonNeural
# - ko-KR-SunHiNeural
#
# 동일 음성을 사용하는 경우 속도(rate)와 음높이(pitch)를
# 다르게 설정해 화자를 구분합니다.
# ------------------------------------------------------------

SPEAKER_VOICES = {
    "김부장": {
        "voice": "ko-KR-HyunsuMultilingualNeural",
        "rate": "-8%",
        "pitch": "-8Hz",
    },

    "정과장": {
        "voice": "ko-KR-InJoonNeural",
        "rate": "-3%",
        "pitch": "-3Hz",
    },

    "김과장": {
        "voice": "ko-KR-InJoonNeural",
        "rate": "+8%",
        "pitch": "+5Hz",
    },

    "이대리": {
        "voice": "ko-KR-SunHiNeural",
        "rate": "-3%",
        "pitch": "-3Hz",
    },

    "박대리": {
        "voice": "ko-KR-SunHiNeural",
        "rate": "+10%",
        "pitch": "+6Hz",
    },
}


# ------------------------------------------------------------
# 2. 주간 공정 회의 대본
# ------------------------------------------------------------

SCRIPT = """
김부장: 자, 시작하겠습니다. 지난주 대비 전체 공정률은 98% 정도로 계획대로 가고 있습니다. 이번 주는 자재 입고하고 현장 작업 일정 쪽에 확인할 게 좀 있는데요. 먼저 자재 입고 현황부터 볼까요?

정과장: 주요 패널 자재는 어제 오후에 1차 입고됐고요. 검수 결과는 특이사항 없었습니다. 일부 부자재가 물류 일정 때문에 내일 오전에 들어올 예정입니다.

이대리: 내일 들어오는 것 중에 B구역 용접할 때 쓰는 고정 브래킷도 포함돼 있나요? 현장에서는 오전 10시 전에는 불출돼야 작업 대기가 안 생긴다고 하던데요.

정과장: 아, 브래킷은 어제 들어왔어요. 창고에 있습니다. 오늘 오후에 불출 요청만 올려주시면 내일 작업 시작 전에 바로 쓸 수 있어요. 내일 들어오는 건 실링 자재 위주라 오전 작업에는 크게 영향 없을 겁니다.

김부장: 그러면 브래킷은 문제없고. 정과장님, 내일 실링 자재 들어오면 바로 확인해서 현장 투입 일정에 차질 없도록 해주세요.

정과장: 네, 입고되는 대로 확인하겠습니다.

김부장: 다음은 현장 쪽 볼게요. 이번 주 야간 연장 작업은 어떻게 잡혀 있죠?

김과장: 3번 구역 배관 라인이 예상보다 반나절 정도 밀렸습니다. 그래서 오늘하고 내일 이틀 동안 두 시간씩 연장 작업하는 걸로 잡았습니다. 추가 인력은 협력사하고 조율 끝났고요.

박대리: 오늘 들어갈 숙련공 여섯 명은 오전에 안전교육하고 작업허가서까지 다 처리했습니다. 그런데 내일 오후부터 비 예보가 있어서 외부 크레인 양중은 가능하면 오전에 끝내는 쪽으로 일정을 좀 당겼습니다.

이대리: 그러면 내일 오전에 양중이 몰리겠네요. B구역 자재 이동하고 동선이 겹칠 수도 있을 것 같은데요. 특히 아홉 시에서 열한 시 사이에는 2번 게이트 쪽이 좀 복잡할 것 같습니다.

김과장: 맞아요. 그건 미리 조정해야겠네요. 이대리하고 박대리가 회의 끝나고 도면 같이 보면서 양중 동선하고 자재 운반 시간대를 30분 단위로 나눠서 다시 맞춰주세요. 정리되면 현장에도 바로 공유해주시고요.

박대리: 네, 회의 끝나고 바로 같이 보겠습니다. 동선표 수정해서 현장 반장님들한테도 공유할게요.

이대리: 네, 시간대 겹치는 부분 먼저 확인해볼게요.

김부장: 좋아요. 비 오기 전에 최대한 작업할 수 있도록 조정하고, 동선 겹치는 부분은 꼭 확인해주세요. 그리고 야간 연장 작업할 때는 관리자가 반드시 상주해서 안전수칙 지켜지는지도 확인해주시고요.

김과장: 네, 그 부분은 제가 확인하겠습니다.

김부장: 다른 특이사항 있습니까?

정과장: 자재 쪽은 없습니다.

김과장: 현장도 현재까지는 없습니다.

김부장: 알겠습니다. 그러면 오늘 나온 것들 일정대로 진행해주시고, 문제 생기면 바로 공유해주세요. 수고하셨습니다.
"""


# ------------------------------------------------------------
# 2-2. 2차 회의 대본 (1차 회의 지시사항 이행점검 시나리오)
#
# 실행: python .\scripts\make_meeting_audio.py 2
# 결과: "2nd meeting.mp3"
# ------------------------------------------------------------

SCRIPT_2 = """
김부장: 자, 시작하겠습니다. 지난 회의에서 몇 가지 확인하기로 한 내용들이 있었는데 그것부터 보고 이번 주 일정 보겠습니다. 자재 쪽부터 볼까요? 지난번 실링 자재 건 어떻게 됐습니까?

정과장: 네, 실링 자재는 예정대로 다음 날 오전에 전량 입고됐습니다. 검수도 끝났고 현장하고 일정 맞춰서 당일 오후에 바로 투입했습니다. 현재까지 자재 때문에 지연된 공정은 없습니다.

김부장: 알겠습니다. 그 건은 완료된 걸로 보면 되겠네요.

정과장: 네, 완료됐습니다.

김부장: 그리고 지난번에 양중 작업하고 B구역 자재 이동 동선 겹치는 문제 있었잖아요. 그건 어떻게 됐어요?

박대리: 이대리님하고 회의 끝나고 바로 다시 확인했습니다. 양중 시간을 오전 아홉 시부터 열한 시까지 30분 단위로 나눴고요. 자재 운반 차량 들어오는 시간하고 겹치지 않도록 동선표도 수정했습니다. 수정된 내용은 현장 반장님들한테 전부 공유했습니다.

이대리: 네, 다음 날 실제 작업할 때도 확인했는데 동선 겹치는 문제는 없었습니다. 2번 게이트 쪽도 통제 인원을 한 명 추가해서 작업 끝날 때까지 운영했습니다.

김부장: 좋아요. 그러면 동선 조정하고 현장 공유하는 것까지는 완료된 거죠?

박대리: 네, 완료했습니다.

김부장: 야간 연장 작업은 어땠습니까? 안전 쪽 문제는 없었고요?

김과장: 네, 이틀 모두 관리자가 현장에 상주했고 안전수칙 준수 여부도 확인했습니다. 별도 사고나 특이사항 없이 계획했던 작업까지 마무리했습니다.

김부장: 좋습니다. 지난번 지시했던 내용들은 일단 잘 마무리됐네요. 그러면 이번 주 공정 보겠습니다. 3번 구역 배관 라인은 지금 어느 정도까지 왔습니까?

김과장: 지난주 연장 작업하면서 지연됐던 부분은 거의 따라잡았습니다. 현재 계획 대비 99% 정도까지 올라왔습니다. 다만 다음 공정 들어가기 전에 배관 라인 최종 검사를 한 번 해야 합니다.

정과장: 검사할 때 자재 쪽에서도 같이 확인해야 할 게 있을까요?

김과장: 지금으로서는 별도로 준비할 건 없습니다. 검사하면서 이상 있는 부분이 나오면 그때 요청드리겠습니다.

김부장: 그러면 김과장님, 3번 구역 배관 라인 최종 검사 이번 주 금요일까지 마무리해주세요. 이상 사항 나오면 바로 정리해서 저한테 공유해주시고요.

김과장: 네, 금요일까지 검사하고 결과 정리해서 공유하겠습니다.

이대리: B구역은 다음 주부터 용접 물량이 조금 늘어날 것 같은데 작업자 배치를 미리 조정해두는 게 좋을 것 같습니다.

김부장: 물량이 얼마나 늘어날지는 나왔어요?

이대리: 아직 확정은 아닙니다. 생산 계획 쪽에서 내일쯤 정확한 물량이 나올 것 같습니다.

김부장: 그럼 지금 인력을 움직이지는 말고 물량 확정되면 다시 판단하죠.

이대리: 네, 알겠습니다.

박대리: 그리고 현장에서 기존 동선표를 작업자들이 바로 확인하기 어렵다는 의견이 조금 있었습니다. 작업 구역 입구에 크게 출력해서 붙여놓으면 보기 편할 것 같기는 합니다.

김과장: 그 방법도 괜찮겠네요.

김부장: 일단 이번 주 운영해보고 필요하면 다음 회의 때 다시 얘기해봅시다.

박대리: 네, 그렇게 하겠습니다.

김부장: 다른 특이사항 있습니까?

정과장: 자재 쪽은 없습니다.

이대리: 현장도 현재까지는 없습니다.

김부장: 알겠습니다. 그러면 김과장님은 금요일까지 3번 구역 배관 라인 최종 검사하고 결과 공유해주세요. 나머지는 현재 계획대로 진행하고 문제 생기면 바로 공유해주시고요. 오늘 회의는 여기까지 하겠습니다. 수고하셨습니다.
"""


# ------------------------------------------------------------
# 3. Edge TTS 음성 생성
#
# Edge TTS 서버에서 간헐적으로 503 오류가 발생할 수 있으므로
# 발화별 최대 5회까지 자동 재시도합니다.
# ------------------------------------------------------------

async def create_tts_with_retry(
    text: str,
    speaker: str,
    config: dict,
    output_file: str,
    max_retries: int = 5,
):
    for attempt in range(1, max_retries + 1):

        try:
            communicate = edge_tts.Communicate(
                text=text,
                voice=config["voice"],
                rate=config["rate"],
                pitch=config["pitch"],
            )

            await communicate.save(output_file)

            return

        except Exception as e:

            # 실패하면서 빈 파일 또는 손상 파일이 만들어졌다면 삭제
            if os.path.exists(output_file):
                try:
                    os.remove(output_file)
                except OSError:
                    pass

            if attempt >= max_retries:
                print()
                print(f"❌ {speaker} 음성 생성 최종 실패")
                print(f"   오류: {e}")
                raise

            # 3초 → 6초 → 9초 → 12초
            wait_seconds = attempt * 3

            print(
                f"   ⚠️ Edge TTS 연결 실패 "
                f"({attempt}/{max_retries})"
            )

            print(
                f"   {wait_seconds}초 후 자동으로 다시 시도합니다..."
            )

            await asyncio.sleep(wait_seconds)


# ------------------------------------------------------------
# 4. 전체 회의 음성 생성
# ------------------------------------------------------------

async def generate_meeting_audio(
    script: str,
    output_filename: str = "weekly_meeting_sample.mp3",
):

    lines = [
        line.strip()
        for line in script.strip().split("\n")
        if line.strip()
    ]

    temp_files = []

    # 최종 오디오
    combined_audio = AudioSegment.empty()

    # 화자 발화 사이 0.5초 무음
    pause_between_speakers = AudioSegment.silent(
        duration=500
    )

    print()
    print("=" * 60)
    print("🎙️ Meeting Action Agent 데모 회의 음성 생성")
    print("=" * 60)
    print(f"총 발화 수: {len(lines)}")
    print()

    try:

        for idx, line in enumerate(lines):

            if ":" not in line:
                print(
                    f"⚠️ 형식을 인식하지 못해 건너뜁니다: {line}"
                )
                continue

            speaker, text = line.split(":", 1)

            speaker = speaker.strip()
            text = text.strip()

            # 등록되지 않은 화자는 기본 음성
            config = SPEAKER_VOICES.get(
                speaker,
                {
                    "voice": "ko-KR-InJoonNeural",
                    "rate": "+0%",
                    "pitch": "+0Hz",
                },
            )

            # 임시 MP3
            temp_file = f"temp_turn_{idx:02d}.mp3"

            temp_files.append(temp_file)

            print(
                f"[{idx + 1}/{len(lines)}] "
                f"{speaker} 녹음 중..."
            )

            # TTS 생성
            await create_tts_with_retry(
                text=text,
                speaker=speaker,
                config=config,
                output_file=temp_file,
            )

            # 생성된 MP3 읽기
            segment = AudioSegment.from_mp3(
                temp_file
            )

            # 최종 오디오에 추가
            combined_audio += segment

            # 발화 사이 0.5초
            combined_audio += pause_between_speakers

            print(
                f"   ✓ {speaker} 완료 "
                f"({len(segment) / 1000:.1f}초)"
            )

            # 서버에 너무 빠르게 연속 요청하지 않도록 잠시 대기
            await asyncio.sleep(1.5)

        print()
        print("🔧 개별 음성을 하나의 회의 MP3로 합치는 중...")

        # 최종 MP3 저장
        combined_audio.export(
            output_filename,
            format="mp3",
            bitrate="192k",
        )

        total_seconds = len(combined_audio) / 1000

        minutes = int(total_seconds // 60)
        seconds = int(total_seconds % 60)

        print()
        print("=" * 60)
        print("✅ 회의 음성 생성 완료!")
        print("=" * 60)

        print(
            f"📁 파일 위치:"
            f"\n   {os.path.abspath(output_filename)}"
        )

        print(
            f"⏱️ 재생 시간: "
            f"{minutes}분 {seconds}초"
        )

        print(
            f"👥 화자 수: "
            f"{len(SPEAKER_VOICES)}명"
        )

        print()
        print(
            "이 MP3를 Meeting Action Agent의 "
            "회의 음성 업로드에 사용할 수 있습니다."
        )

        print("=" * 60)

    finally:

        # ----------------------------------------------------
        # 임시 MP3 정리
        # ----------------------------------------------------

        for temp_file in temp_files:

            if os.path.exists(temp_file):

                try:
                    os.remove(temp_file)

                except OSError:
                    pass


# ------------------------------------------------------------
# 5. 실행
# ------------------------------------------------------------

if __name__ == "__main__":
    import sys

    # 인자 없음: 1차 회의(weekly_meeting_sample.mp3) / 인자 "2": 2차 회의("2nd meeting.mp3")
    if len(sys.argv) > 1 and sys.argv[1] == "2":
        asyncio.run(generate_meeting_audio(SCRIPT_2, "2nd meeting.mp3"))
    else:
        asyncio.run(
            generate_meeting_audio(
                SCRIPT,
                "weekly_meeting_sample.mp3",
            )
        )