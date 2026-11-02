index_file = '/home/ryzen/projects/claude-agents/claims_ai/views/web/index.html'
with open(index_file, 'r', encoding='utf-8') as f:
    c = f.read()
print('index.html size:', len(c))