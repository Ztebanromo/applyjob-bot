import json
with open('data/pending_questions.json', encoding='utf-8') as f:
    qs = json.load(f)
pending = [q for q in qs if not q.get('answered')]
print(f'Pendientes: {len(pending)}')
for i, q in enumerate(pending):
    portal = q.get('portal', '?')
    question = q.get('question', '')
    print(f'{i}: [{portal}] {question}')
