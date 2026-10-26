"""Long-term memory storage, search, index, and reset assertions."""

import os, shutil, tempfile
import memory as mem
from agent import ReActAgent

mem_dir = tempfile.mkdtemp()
mem_path = os.path.join(mem_dir, "notes.jsonl")

# Test invariant or expected behavior.
assert "作者" in mem._terms("作者用中文提问"), "中文没有二元组 —— 检索会整体失效"
assert "中文" in mem._terms("作者用中文提问")
assert mem._terms("   ") == set()
assert mem._terms("a b cd") == {"cd"}, mem._terms("a b cd")
print("9a. 中文二元组 + 拉丁词；单字符词被丢掉（空格分词对中文无效）")

# Test invariant or expected behavior.
assert mem.load(mem_path) == []
assert mem.render_index(mem_path) == "", "空库不该产生索引块"
assert "empty" in mem.render_recall(*mem.search("任意", path=mem_path), query="任意")
print("9b. 空库：load=[]，索引为空串，召回明确说'空'而不是返回空串")

# Test invariant or expected behavior.
ok, _ = mem.append("第一条", "正文一", "user", path=mem_path)
assert ok and mem.load(mem_path)[0]["id"] == 1
ok, _ = mem.append("第二条", "", "project", path=mem_path)
assert ok and [n["id"] for n in mem.load(mem_path)] == [1, 2]
print("9c. 写入两条，id 依次为 1 / 2")

# Test invariant or expected behavior.
before = len(mem.load(mem_path))
ok, _ = mem.append("第一条", "不一样的正文", "user", path=mem_path)
assert ok is False and len(mem.load(mem_path)) == before
print("9d. 重复 summary 不写入（写入路径幂等）")

# Test invariant or expected behavior.
mem.append("conda 解算器会重写整个环境", "别用它修包", "project", path=mem_path)
matched, rows = mem.search("第一条", path=mem_path)
assert matched and rows[0][1]["summary"] == "第一条" and rows[0][0] == 1.0
print("9e. 全串命中得 1.0 且排第一")

# Test invariant or expected behavior.
matched, rows = mem.search("量子色动力学", k=2, path=mem_path)
assert matched is False and len(rows) == 2
assert mem.render_recall(matched, rows, "量子色动力学").startswith("No note matched")
print("9f. 无命中时回退最近 k 条并标注 matched=False")

# Test invariant or expected behavior.
idx = mem.render_index(mem_path, max_notes=10, max_chars=10000)
assert idx.count("\n") == 2 and "not shown" not in idx
idx = mem.render_index(mem_path, max_notes=1, max_chars=10000)
assert idx.count("\n") == 1 and "(2 more not shown" in idx, idx
assert "conda" in idx, "上限应该保留最新的，挤掉最旧的"
idx = mem.render_index(mem_path, max_notes=10, max_chars=10)
assert "not shown" in idx and len(idx) < 200 and not idx.startswith("\n"), idx
print("9g. 索引上限：按条数、按字符数都生效，截断提示一定出现")

# Test invariant or expected behavior.
from config import MEMORY_INDEX_MAX_NOTES, MEMORY_INDEX_MAX_CHARS
assert mem.INDEX_MAX_NOTES == MEMORY_INDEX_MAX_NOTES
assert mem.INDEX_MAX_CHARS == MEMORY_INDEX_MAX_CHARS
print("9h. memory.py 的默认上限与 config.py 的真值一致")

# Test invariant or expected behavior.
real_path = mem.NOTES_PATH
mem.NOTES_PATH = mem_path
try:
    ag9 = ReActAgent()
    ag9.reset()
finally:
    mem.NOTES_PATH = real_path
assert ag9.memory_index and "conda" in ag9.memory_index
assert len(ag9.messages) == 1 and ag9.messages[0]["role"] == "system"
assert ag9.memory_index in ag9.messages[0]["content"]
assert ag9.memory_index not in ag9.system_prompt, "索引不该污染基础 prompt"
print("9i. reset()：索引拼进 messages[0]，历史仍只有 1 条，基础 prompt 未被污染")

# Test invariant or expected behavior.
# Test invariant or expected behavior.
idx = mem.render_index(mem_path, max_notes=10, max_chars=10000)
assert "[1]" in idx and "第一条" in idx, idx        # Test invariant or expected behavior.
before = [n["id"] for n in mem.load(mem_path)]
ok, msg = mem.forget(2, path=mem_path)
assert ok is True and mem.forget(2, path=mem_path)[0] is False, "第二次删同一条居然还成功"
assert [n["id"] for n in mem.load(mem_path)] == [1, 3], mem.load(mem_path)
assert len(mem.render_index(mem_path, 10, 10000).splitlines()) == 2, "删完索引没跟着变"
# Test invariant or expected behavior.
snapshot = open(mem_path, encoding="utf-8").read()
ok, msg = mem.forget(99, path=mem_path)
assert ok is False and "99" in msg, msg
assert open(mem_path, encoding="utf-8").read() == snapshot, "删不存在的 id 却改了文件"
# Test invariant or expected behavior.
mem.append("第四条", "", "project", path=mem_path)
assert [n["id"] for n in mem.load(mem_path)] == [1, 3, 4], mem.load(mem_path)
print("9j. 删除：索引带 id；删一条其余不动；删不存在的 id 明确失败；id 不复用")

# Test invariant or expected behavior.
# Test invariant or expected behavior.
# Test invariant or expected behavior.
for n in mem.load(mem_path):
    mem.forget(n["id"], path=mem_path)
assert mem.load(mem_path) == []
assert mem.render_index(mem_path) == "" and mem.forget(1, path=mem_path)[0] is False
mem.append("清空后重来", "", "project", path=mem_path)
assert mem.load(mem_path)[0]["id"] == 1, mem.load(mem_path)
print("9j-2. 删到空库后 id 从 1 重来（已固定，见 forget() 的 docstring）")

shutil.rmtree(mem_dir, ignore_errors=True)

# Test invariant or expected behavior.
# Test invariant or expected behavior.
# Test invariant or expected behavior.
# Test invariant or expected behavior.
# Test invariant or expected behavior.
