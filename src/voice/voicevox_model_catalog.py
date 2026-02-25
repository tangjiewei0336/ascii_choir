"""
VVM 音声模型目录
来源: https://github.com/VOICEVOX/voicevox_vvm
"""
from dataclasses import dataclass
from typing import Optional

# 使用 latest 始终下载最新版；固定版本仅作回退
VVM_RELEASE = "0.16.3"
VVM_BASE = f"https://github.com/VOICEVOX/voicevox_vvm/releases/download/{VVM_RELEASE}"
VVM_LATEST_BASE = "https://github.com/VOICEVOX/voicevox_vvm/releases/latest/download"


@dataclass
class VVMEntry:
    """单个 VVM 文件条目"""
    filename: str
    desc: str  # 包含的角色简要描述
    supports_sing: bool  # 是否支持歌唱
    size_mb: int = 55  # 约大小 MB


# 可选下载的 VVM 列表（歌唱优先，s0 必含波音リツ等）
VVM_CATALOG: list[VVMEntry] = [
    VVMEntry("s0.vvm", "歌唱用：波音リツ、四国めたん、ずんだもん、九州そら等 30+ 角色", supports_sing=True, size_mb=120),
    VVMEntry("0.vvm", "对话用：四国めたん、ずんだもん、春日部つむぎ、雨晴はう", supports_sing=False, size_mb=56),
    VVMEntry("1.vvm", "对话用：冥鳴ひまり", supports_sing=False, size_mb=57),
    VVMEntry("2.vvm", "对话用：九州そら", supports_sing=False, size_mb=55),
    VVMEntry("3.vvm", "对话用：波音リツ、中国うさぎ", supports_sing=False, size_mb=55),
    VVMEntry("4.vvm", "对话用：玄野武宏、剣崎雌雄", supports_sing=False, size_mb=55),
    VVMEntry("5.vvm", "对话用：四国めたん、ずんだもん、九州そら（ささやき等）", supports_sing=False, size_mb=55),
    VVMEntry("6.vvm", "对话用：No.7", supports_sing=False, size_mb=55),
    VVMEntry("7.vvm", "对话用：後鬼", supports_sing=False, size_mb=55),
    VVMEntry("8.vvm", "对话用：WhiteCUL", supports_sing=False, size_mb=55),
    VVMEntry("9.vvm", "对话用：白上虎太郎", supports_sing=False, size_mb=55),
    VVMEntry("10.vvm", "对话用：玄野武宏、ちび式じい", supports_sing=False, size_mb=55),
    VVMEntry("11.vvm", "对话用：櫻歌ミコ、ナースロボ＿タイプＴ", supports_sing=False, size_mb=57),
    VVMEntry("12.vvm", "对话用：†聖騎士 紅桜†、雀松朱司、麒ヶ島宗麟", supports_sing=False, size_mb=55),
    VVMEntry("13.vvm", "对话用：春歌ナナ、猫使アル、猫使ビィ", supports_sing=False, size_mb=55),
    VVMEntry("14.vvm", "对话用：栗田まろん、あいえるたん、満別花丸、琴詠ニア", supports_sing=False, size_mb=62),
    VVMEntry("15.vvm", "对话用：ずんだもん、青山龍星、もち子さん、小夜/SAYO", supports_sing=False, size_mb=63),
    VVMEntry("16.vvm", "对话用：後鬼（怒り、鬼ver.）", supports_sing=False, size_mb=55),
    VVMEntry("17.vvm", "对话用：Voidoll", supports_sing=False, size_mb=55),
    VVMEntry("18.vvm", "对话用：ぞん子、中部つるぎ", supports_sing=False, size_mb=55),
    VVMEntry("19.vvm", "对话用：離途、黒沢冴白", supports_sing=False, size_mb=55),
    VVMEntry("20.vvm", "对话用：ユーレイちゃん", supports_sing=False, size_mb=55),
    VVMEntry("21.vvm", "对话用：東北ずん子、東北きりたん、東北イタコ、猫使アル/ビィ", supports_sing=False, size_mb=55),
    VVMEntry("22.vvm", "对话用：あんこもん", supports_sing=False, size_mb=55),
]


def get_vvm_url(filename: str, use_latest: bool = True) -> str:
    """获取 VVM 文件的下载 URL。use_latest=True 时使用最新版，否则用固定版本"""
    base = VVM_LATEST_BASE if use_latest else VVM_BASE
    return f"{base}/{filename}"
