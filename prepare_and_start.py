import json, os, subprocess, sys
BASE=os.path.dirname(os.path.abspath(__file__))
main_path=os.path.join(BASE,'hadiths.json')
bux_path=os.path.join(BASE,'buxoriy_1_2.json')
if os.path.exists(bux_path):
    with open(main_path,encoding='utf-8') as f: main=json.load(f)
    with open(bux_path,encoding='utf-8') as f: bux=json.load(f)
    # Preserve the existing 101-hadis base exactly. Append Buxoriy 1-2 only once.
    existing_sources={str(h.get('source','')) for h in main}
    if not any('Sahihi Buxoriy — 1-jild' in s for s in existing_sources):
        offset=max((int(h.get('id',0)) for h in main),default=0)
        out=list(main)
        for i,h in enumerate(bux,1):
            x=dict(h)
            x['id']=offset+i
            x['sequence']=offset+i
            out.append(x)
        tmp=main_path+'.tmp'
        with open(tmp,'w',encoding='utf-8') as f: json.dump(out,f,ensure_ascii=False,indent=2)
        os.replace(tmp,main_path)
        print(f'Hadith baza yangilandi: {len(main)} + {len(bux)} = {len(out)}')
    else:
        print('Buxoriy 1-2 allaqachon qo‘shilgan; qayta qo‘shilmadi.')
# Run the original bot without changing its source code.
os.execv(sys.executable,[sys.executable,os.path.join(BASE,'bot.py')])
