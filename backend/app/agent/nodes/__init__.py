"""主图节点子包。每个节点复用既有检索/LLM 代码，行为与 legacy stream_chat 同构。

留空 __init__（不 re-export 节点函数）以避免子模块名与同名函数冲突——
graph.py 直接从子模块导入，测试也能按 ``app.agent.nodes.<mod>.<name>`` 打补丁。
"""
