from __future__ import annotations

import json
from pathlib import Path


BUNDLE_ITEMS = [
    {
        "id": "01",
        "slug": "pi0_7",
        "topic": "π0.7: A Steerable Generalist Robotic Foundation Model with Emergent Capabilities",
        "topic_summary": "通用机器人基础模型，强调可控指令跟随、跨 embodiment 泛化，以及从多样机器人与非机器人数据中涌现出的新任务能力。",
        "project_url": "https://arxiv.org/abs/2604.15483",
        "youtube_url": "https://www.youtube.com/watch?v=cPTpVmt7gYE",
        "instruction": "Create a slide-based presentation explaining π0.7, focusing on how a steerable generalist robotic foundation model achieves zero-shot generalization, follows diverse instructions, and demonstrates emergent capabilities across novel manipulation tasks.",
    },
    {
        "id": "02",
        "slug": "EgoScale",
        "topic": "EgoScale: Scaling Dexterous Manipulation with Diverse Egocentric Human Data",
        "topic_summary": "利用大规模第一视角人类操作视频提升灵巧操作学习，重点在 hand-object interaction、示范数据规模化和机器人泛化能力。",
        "project_url": "https://research.nvidia.com/labs/gear/egoscale/",
        "youtube_url": "https://www.youtube.com/watch?v=3yRd6uo85eU",
        "instruction": "Create a slide-based presentation explaining EgoScale, focusing on how diverse egocentric human videos are used to scale dexterous manipulation learning and why this data source improves robot hand-object interaction.",
    },
    {
        "id": "03",
        "slug": "DexWM",
        "topic": "World Models Can Leverage Human Videos for Dexterous Manipulation",
        "topic_summary": "DexWM 世界模型从人类手部视频中学习精细手物交互动力学，用于零样本灵巧抓取、放置和到达任务。",
        "project_url": "https://raktimgg.github.io/dexwm/",
        "youtube_url": "https://www.youtube.com/watch?v=DAHnrQOh0HA",
        "instruction": "Create a slide-based presentation explaining DexWM, focusing on how a world model can learn dexterous manipulation from human videos and how this improves planning and zero-shot robot execution.",
    },
    {
        "id": "04",
        "slug": "ACoT_VLA",
        "topic": "ACoT-VLA: Action Chain-of-Thought for Vision-Language-Action Models",
        "topic_summary": "在 action space 中显式和隐式推理，让 VLA 不只看图和语言，还能先形成粗粒度动作意图，再生成最终控制动作。",
        "project_url": "https://github.com/AgibotTech/ACoT-VLA",
        "youtube_url": "https://www.youtube.com/watch?v=43yUDoGKh9w",
        "instruction": "Create a slide-based presentation explaining ACoT-VLA, focusing on why vision-language-action models need action-space reasoning and how explicit and implicit action chain-of-thought improve manipulation performance.",
    },
    {
        "id": "05",
        "slug": "GR_RL",
        "topic": "GR-RL: Going Dexterous and Precise for Long-Horizon Robotic Manipulation",
        "topic_summary": "把通用 VLA 专门强化成长时程、高精度的灵巧操作策略，适合展示鞋带、布料等需要细致控制的机器人任务。",
        "project_url": "https://seed.bytedance.com/gr_rl",
        "youtube_url": "https://www.youtube.com/watch?v=3fUuewqcd4A",
        "instruction": "Create a slide-based presentation explaining GR-RL, focusing on how a generalist VLA policy is adapted into a more dexterous and precise specialist for long-horizon robotic manipulation.",
    },
    {
        "id": "06",
        "slug": "Pragmatic_VLA",
        "topic": "A Pragmatic VLA Foundation Model",
        "topic_summary": "面向真实世界机器人操作的实用型 VLA 基础模型，强调双臂平台、真实数据、工程可落地性和稳健泛化。",
        "project_url": "https://technology.robbyant.com/lingbot-vla",
        "youtube_url": "https://www.youtube.com/watch?v=-6pCooFH1Ug",
        "instruction": "Create a slide-based presentation explaining A Pragmatic VLA Foundation Model, focusing on the practical design choices, real-world robot data, and why the model is useful for robust manipulation deployment.",
    },
    {
        "id": "07",
        "slug": "GigaBrain_0_5M",
        "topic": "GigaBrain-0.5M: a VLA That Learns From World Model-Based Predictions",
        "topic_summary": "把 world model 预测引入 VLA 强化学习过程，提升复杂操作任务中的长时程成功率与视觉前瞻能力。",
        "project_url": "https://gigabrain05m.github.io/",
        "youtube_url": "https://www.youtube.com/watch?v=9WofNo-8v-0",
        "instruction": "Create a slide-based presentation explaining GigaBrain-0.5M, focusing on how world model-based predictions improve vision-language-action training and lead to stronger long-horizon manipulation behaviors.",
    },
    {
        "id": "08",
        "slug": "GR00T_N1",
        "topic": "GR00T N1: An Open Foundation Model for Generalist Humanoid Robots",
        "topic_summary": "NVIDIA 面向 humanoid 的开放基础模型，强调双系统认知架构、全身动作技能与人形机器人通用能力。",
        "project_url": "https://research.nvidia.com/labs/lpr/publication/gr00tn1_2025/",
        "youtube_url": "https://www.youtube.com/watch?v=50uqOQrPN1Y",
        "instruction": "Create a slide-based presentation explaining GR00T N1, focusing on how an open humanoid foundation model supports generalist whole-body robot behavior and why this matters for physical AI.",
    },
    {
        "id": "09",
        "slug": "Genie_Envisioner",
        "topic": "Genie Envisioner: A Unified World Foundation Platform for Robotic Manipulation",
        "topic_summary": "统一的机器人操作 world foundation platform，把策略学习、视频生成、模拟和评估放到同一套 video-based world model 框架里。",
        "project_url": "https://genie-envisioner.github.io/",
        "youtube_url": "https://www.youtube.com/watch?v=EiCwyq6H808",
        "instruction": "Create a slide-based presentation explaining Genie Envisioner, focusing on how a video-generative world foundation platform unifies policy learning, evaluation, and simulation for robotic manipulation.",
    },
    {
        "id": "10",
        "slug": "GenMimic",
        "topic": "From Generated Human Videos to Physically Plausible Robot Trajectories",
        "topic_summary": "先生成或利用人类动作视频，再提取 3D 关键点并映射到机器人可执行的物理合理轨迹，连接视频生成与机器人控制。",
        "project_url": "https://genmimic.github.io/",
        "youtube_url": "https://www.youtube.com/watch?v=FkQ-PP1qZ8Y",
        "instruction": "Create a slide-based presentation explaining how generated human videos can be turned into physically plausible robot trajectories, focusing on pose extraction, retargeting, and physics-aware execution.",
    },
    {
        "id": "11",
        "slug": "WholeBodyLocomotion",
        "topic": "Learning Whole-Body Humanoid Locomotion via Motion Generation and Motion Tracking",
        "topic_summary": "将动作生成与动作跟踪结合，用于具身感知驱动的人形机器人全身地形适应运动控制。",
        "project_url": "https://wholebodylocomotion.github.io/",
        "youtube_url": "https://www.youtube.com/watch?v=5fjcu3DrXdE",
        "instruction": "Create a slide-based presentation explaining whole-body humanoid locomotion via motion generation and motion tracking, focusing on terrain-aware adaptation, motion priors, and real robot deployment.",
    },
    {
        "id": "12",
        "slug": "RynnVLA_002",
        "topic": "RynnVLA-002: A Unified Vision-Language-Action and World Model",
        "topic_summary": "把 VLA 和 world model 合到一个统一框架中，让动作生成和未来视觉预测相互促进，服务于机器人操作与环境建模。",
        "project_url": "https://rynnvla.github.io",
        "youtube_url": "https://www.youtube.com/watch?v=azcMMG9cjqc",
        "instruction": "Create a slide-based presentation explaining RynnVLA-002, focusing on how a unified vision-language-action and world model jointly improves control and future-state prediction.",
    },
    {
        "id": "13",
        "slug": "MultiWorld",
        "topic": "MultiWorld: Scalable Multi-Agent Multi-View Video World Models",
        "topic_summary": "可扩展的多智能体、多视角视频世界模型，强调多视角一致性、多人或多机器人交互建模，以及可控视频 rollout。",
        "project_url": "https://multi-world.github.io/",
        "youtube_url": "https://www.youtube.com/watch?v=7t1n9b20gEY",
        "instruction": "Create a slide-based presentation explaining MultiWorld, focusing on how a multi-agent multi-view video world model achieves controllable rollouts and cross-view consistency for interactive environments.",
    },
    {
        "id": "14",
        "slug": "GeoPT",
        "topic": "GeoPT: Scaling Physics Simulation via Lifted Geometric Pre-Training",
        "topic_summary": "把几何预训练提升到带合成动力学的物理模拟预训练，以更少标签实现更强的流体和刚体模拟能力。",
        "project_url": "https://github.com/Physics-Scaling/GeoPT",
        "youtube_url": "https://www.youtube.com/watch?v=0LpljdvPO2k",
        "instruction": "Create a slide-based presentation explaining GeoPT, focusing on how lifted geometric pre-training scales neural physics simulation and bridges the gap between static geometry and dynamics-aware modeling.",
    },
    {
        "id": "15",
        "slug": "Embodied_AI_From_LLMs_to_World_Models",
        "topic": "Embodied AI: From LLMs to World Models",
        "topic_summary": "具身智能综述，系统梳理 LLM 驱动和 world model 驱动两条路线，以及它们在规划、交互和物理泛化中的角色。",
        "project_url": "https://arxiv.org/abs/2509.20021",
        "youtube_url": "https://www.youtube.com/watch?v=Zvh6gSBNvDk",
        "instruction": "Create a slide-based presentation explaining the evolution of embodied AI from large language models to world models, focusing on the different roles of reasoning, planning, and predictive world understanding.",
    },
]


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    bundle_root = repo_root / "robot_vla_worldmodel_batch15"
    bundle_root.mkdir(parents=True, exist_ok=True)

    summary = []
    for item in BUNDLE_ITEMS:
        item_dir = bundle_root / item["slug"]
        item_dir.mkdir(parents=True, exist_ok=True)
        meta = {
            "id": item["id"],
            "slug": item["slug"],
            "topic": item["topic"],
            "topic_summary": item["topic_summary"],
            "instruction": item["instruction"],
            "url": item["project_url"],
            "normalized_source_url": item["project_url"],
            "youtube_url": item["youtube_url"],
        }
        (item_dir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (item_dir / "instruction.txt").write_text(item["instruction"], encoding="utf-8")
        (item_dir / "topic_summary.txt").write_text(item["topic_summary"], encoding="utf-8")
        summary.append(meta)

    (bundle_root / "bundle_manifest.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(bundle_root)


if __name__ == "__main__":
    main()
