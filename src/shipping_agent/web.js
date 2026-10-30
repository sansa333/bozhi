'use strict';
const $ = (selector) => document.querySelector(selector);
const state = {files:[], existing:'[]', report:null, filter:'ALL', busy:false, jobId:null,
  token:'', limits:{max_file_bytes:20971520,max_batch_bytes:104857600,max_documents:100}, poll:0, timer:0};
const operations = {CREATE:'建议新增',UPDATE:'建议更新',NO_CHANGE:'无需变更',PENDING_REVIEW:'需要确认'};
const fields = {'document_info.document_type':'单据类型','document_info.reference_no':'单据编号',
 'document_info.document_date':'单据日期','document_info.bill_of_lading':'提单号','document_info.booking_no':'订舱号',
 'document_info.container_no':'箱号','transaction_party.issuer':'签发方','transaction_party.recipient':'接收方',
 'transaction_party.payer':'付款方','transaction_party.payee':'收款方','charge_details.original_charge_item':'原始费用名称',
 'charge_details.charge_item':'标准费用名称','charge_details.amount':'金额','charge_details.currency':'币种',
 'charge_details.payment_term':'付款约定','charge_details.quantity':'数量','charge_details.unit_price':'单价',
 'charge_details.line_id':'费用行编号'};
const issues = {
 LOCAL_PREVIEW_REQUIRES_EXAM_OCR:'此文件使用本地预览解析，仍需要通过指定文档服务复核。',
 OCR_PUBLIC_URL_REQUIRED:'文档服务还无法读取这份文件。请在连接设置中补充文件访问清单。',
 OCR_STRUCTURED_WITHOUT_ORIGINAL_TEXT_REQUIRES_REVIEW:'文档服务返回了结构化信息，尚需对照原始单据核实。',
 ACCOUNTING_ENTITY_REQUIRED:'请确认费用记在哪家公司，以便判断应收或应付。',
 AR_AP_DIRECTION_UNRESOLVED:'目前还不能确定收付款方向，请对照交易双方确认。',
 INVALID_OR_MISSING_CURRENCY:'币种缺失或无法可靠确认，请查看原文后补充。',
 MISSING_AMOUNT:'这条记录未能提取到可靠金额。', MISSING_CHARGE_NAME:'还不能确定费用名称。',
 MISSING_DOCUMENT_IDENTITY:'没有找到足够明确的单据号、提单号或订舱号。',
 NO_CHARGE_REVIEW_REQUIRED:'这份材料未提取到实际费用，请确认它是否仅为业务说明或提单。',
 LOW_CONFIDENCE:'部分信息的提取把握较低，需要你核实。',
 QUANTITY_RATE_MISMATCH:'数量与单价计算出的金额不一致。',
 MERGED_QUANTITY_RATE_MISMATCH:'更新后的金额与原账单的数量、单价不一致。',
 AMBIGUOUS_BILL_MATCH:'匹配到了多条可能的旧账单，暂时不能确定更新哪一条。',
 MATCH_IDENTITY_CONFLICT:'交易方或记账主体与旧账单存在冲突。',
 CROSS_SOURCE_CONFLICT_OR_AMBIGUOUS_ROW:'不同材料中的同一费用存在差异，或费用行不能唯一对应。',
 MULTIPLE_PROPOSALS_FOR_ONE_EXISTING_BILL:'有多份材料指向同一条旧账单，请确认变更顺序。',
 IDENTICAL_ROWS_REQUIRE_REVIEW:'发现内容相同的费用行，请确认是重复提取还是不同的真实费用。',
 DUPLICATE_SOURCE_ROW:'同一来源行被重复提取，需要检查。',
 CREDIT_NOTE_ACCOUNTING_POLICY_REQUIRED:'这笔费用涉及贷项或负金额，需要确认会计处理方式。',
 UNKNOWN_PAYMENT_TERM:'付款约定未能匹配已知类型，请查看原文。',
 TEXT_TRUNCATED:'文件文字超过本次处理上限，部分内容尚未读取。',
 DOCUMENT_RESOURCE_LIMIT:'文件或附件超过本次处理上限，请拆分后重新上传。',
 BATCH_DOCUMENT_LIMIT:'本批文档数量超过上限，请拆分批次。',
 EXCEL_UNCACHED_FORMULA:'表格中的公式没有保存计算结果，请用 Excel 重新计算并保存。',
 DOC_REQUIRES_LIBREOFFICE:'当前环境暂不能转换旧版 Word 文件，请另存为 DOCX 后上传。',
 UNSUPPORTED_FORMAT:'暂不支持这种文件格式。', EMPTY_DOCUMENT:'没有读取到可处理的内容。',
 INVALID_CHANGE_AUTHORIZATION:'未找到可靠的修改指令，旧账单字段已保留。',
 UNSUPPORTED_CHANGE_PATH:'修改要求包含不允许自动更改的字段，需要人工确认。'
};
function icon(name, cls='') { const node=document.createElementNS('http://www.w3.org/2000/svg','svg');
  if(cls)node.setAttribute('class',cls);const use=document.createElementNS(node.namespaceURI,'use');
  use.setAttribute('href','#i-'+name);node.append(use);return node; }
function el(tag, cls='', text) {const node=document.createElement(tag);if(cls)node.className=cls;
  if(text!==undefined)node.textContent=String(text);return node;}
function toast(message,error=false){const t=$('#toast');t.textContent=message;t.classList.toggle('error',error);t.hidden=false;
  clearTimeout(t._timer);t._timer=setTimeout(()=>{t.hidden=true;},5000);}
function explain(issue){if(issues[issue])return issues[issue];const [code,...rest]=issue.split(':');
  const detail=rest.join(':');if(code==='UNGROUNDED_FIELD')return `“${fields[detail]||detail.replace('totals.','合计 ').replace('tax.','税额 ')}”的原文依据还不充分，需要核实。`;
  if(code==='UNAUTHORIZED_DIFFERENCE')return `“${fields[detail]||detail}”与旧账单不同，但未找到明确修改指令，原值已保留。`;
  if(code==='TOTAL_MISMATCH')return `${detail} 明细与合计金额不一致，请检查是否遗漏、重复或包含税费。`;
  if(code==='EXTRACTION_FAILED')return '这份材料未能可靠提取，请检查文件内容后重试。';
  if(code==='PARSE_FAILED')return '读取文件时遇到问题，请检查文件是否完整，或转换格式后重试。';
  return issue;}
function humanSize(size){return size>=1048576?(size/1048576).toFixed(1)+' MB':Math.max(1,Math.round(size/1024))+' KB';}
function extOf(file){return file.name.split('.').pop().toLowerCase();}
function typeClass(ext){if(ext==='eml')return 'type-mail';if(ext==='pdf')return 'type-pdf';
  if(['xls','xlsx'].includes(ext))return 'type-sheet';if(['doc','docx'].includes(ext))return 'type-word';return 'type-image';}
function tokenHeaders(){return state.token?{Authorization:'Bearer '+state.token}:{};}
async function request(url,options={}){const response=await fetch(url,{...options,headers:{...tokenHeaders(),...(options.headers||{})}});
  if(!response.ok){let message='暂时无法连接工作空间，请稍后重试。';try{const data=await response.json();if(typeof data.detail==='string')message=data.detail;}catch(_){}
    if(response.status===401){$('#service-text').textContent='需要访问凭据';$('#service-dot').classList.remove('online');}
    const error=new Error(message);error.status=response.status;throw error;}return response;}
async function connect(){try{const data=await(await request('/api/workspace')).json();state.limits=data;
  if(!$('#entity').value&&data.accounting_entity)$('#entity').value=data.accounting_entity;
  $('#file-limits').textContent=`支持 EML、PDF、图片、Excel 和 Word · 单个文件最大 ${Math.round(data.max_file_bytes/1048576)} MB`;
  $('#service-dot').classList.add('online');$('#service-text').textContent=data.model_configured?'工作空间已连接':'请先配置模型服务';
}catch(err){$('#service-dot').classList.remove('online');$('#service-text').textContent='工作空间待连接';toast(err.message,true);}}
function addFiles(files){if(state.busy)return;let added=0;let skipped='';
  for(const file of files){const ext=extOf(file);if(!['eml','pdf','png','jpg','jpeg','tiff','tif','xls','xlsx','doc','docx'].includes(ext)){
      skipped=`“${file.name}”的格式暂不支持，请上传邮件、PDF、图片或 Office 文档。`;continue;}
    if(file.size>state.limits.max_file_bytes){skipped=`“${file.name}”超过单个文件大小上限。`;continue;}
    if(state.files.some(f=>f.name===file.name&&f.size===file.size&&f.lastModified===file.lastModified)){
      skipped='同名、同大小的文件已在清单里，无需重复添加。';continue;}
    if(state.files.length>=state.limits.max_documents||state.files.reduce((n,f)=>n+f.size,0)+file.size>state.limits.max_batch_bytes){
      skipped='这批文件已达到处理上限，剩余文件请分批上传。';break;}
    state.files.push(file);added++;}
  renderFiles();if(skipped)toast((added?`已添加 ${added} 个文件。`:'')+skipped,true);
  else if(added)toast(`已收到 ${added} 个文件。你可以继续添加，或直接开始整理。`);}
function renderFiles(){const list=$('#file-list');list.replaceChildren();$('#selected-files').hidden=!state.files.length;
  $('#file-count').textContent=`已选 ${state.files.length} 个文件 · ${humanSize(state.files.reduce((n,f)=>n+f.size,0))}`;
  state.files.forEach((file,index)=>{const item=el('li','file-item');const type=el('span','file-type '+typeClass(extOf(file)),extOf(file).toUpperCase());
    const description=el('div','file-description');const name=el('p','file-name',file.name);name.title=file.name;
    description.append(name,el('p','file-size',humanSize(file.size)));const remove=el('button','icon-button');remove.type='button';
    remove.setAttribute('aria-label','移除 '+file.name);remove.disabled=state.busy;remove.append(icon('close'));
    remove.addEventListener('click',()=>{state.files.splice(index,1);renderFiles();});item.append(type,description,remove);list.append(item);});
  $('#submit').disabled=state.busy||!state.files.length;$('#submit span').textContent=state.busy?'正在整理…':state.files.length?`整理 ${state.files.length} 个文件`:'开始整理';
  $('#dropzone').classList.toggle('disabled',state.busy);$('#dropzone').setAttribute('aria-disabled',String(state.busy));
  for(const selector of ['#entity','#clear-files','#existing-file','#remove-existing','#new-batch'])$(selector).disabled=state.busy;}
function setBusy(value){state.busy=value;renderFiles();const status=$('.companion-top>div>span');
  status.replaceChildren(el('i'),document.createTextNode(value?'正在帮你整理':'准备好帮你了'));}
function navigate(view){$('#work-view').hidden=view!=='work';$('#history-view').hidden=view!=='history';
  for(const name of ['work','history']){const b=$('#nav-'+name);b.classList.toggle('active',view===name);
    if(view===name)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');}
  $('#page-label').textContent=view==='history'?'处理记录':'单据整理';if(view==='history')loadHistory();}
function showDialog(id){const d=$(id);if(!d.open)d.showModal();}
function timeLabel(seconds){const n=Math.max(0,Math.floor(seconds));return n<60?`${n} 秒`:`${Math.floor(n/60)} 分 ${n%60} 秒`;}
function startClock(createdAt){clearInterval(state.timer);const update=()=>{$('#elapsed').textContent=timeLabel(Date.now()/1000-createdAt);};update();state.timer=setInterval(update,1000);}
function updateProgress(event){const messages={queued:['我收到了，正在准备整理。','文件已上传，马上开始查看。'],
 reading:['我正在打开你的单据。','读取文件内容，并查看邮件中的附件。'],
 extracting:['我正在逐条整理费用。','提取单据编号、费用项目、金额和币种。复杂材料可能需要多等一会儿。'],
 checking:['我正在核对这份单据。','检查费用字段、原文依据，以及与旧账单的对应关系。'],
 checking_batch:['费用提取完成，再一起核对一下。','检查不同材料之间是否有重复、冲突或不明确的内容。'],
 saving:['核对完成，正在保存整理结果。','稍后就可以逐条查看依据，并下载结果。'],
 completed:['这批单据已经整理好了。','有疑问的内容已标出来，你可以继续查看和核对。']};
  const [title,description]=messages[event.stage]||messages.queued;$('#progress-title').textContent=title;$('#progress-description').textContent=description;
  $('#progress-file').textContent=event.filename?`${event.file_index?`文件 ${event.file_index} / ${event.file_total} · `:''}${event.filename}`:'';
  const index={queued:0,reading:0,extracting:1,checking:2,checking_batch:2,saving:2,completed:3}[event.stage]??0;
  document.querySelectorAll('.stage-track>div').forEach((node,i)=>{node.classList.toggle('active',i===index);node.classList.toggle('done',i<index);});}
async function pollJob(){clearTimeout(state.poll);if(!state.jobId)return;
  try{const job=await(await request('/api/jobs/'+state.jobId)).json();$('#reconnect').hidden=true;
    if(job.status==='completed'){clearInterval(state.timer);updateProgress({stage:'completed'});
      const report=await(await request('/api/runs/'+job.run_id)).json();state.jobId=null;sessionStorage.removeItem('bozhi-active-job');
      $('#progress-section').classList.add('completed');setBusy(false);renderReport(report,true);return;}
    if(job.status==='failed'){state.jobId=null;sessionStorage.removeItem('bozhi-active-job');throw new Error(job.error||'本次整理未能完成，请稍后重试。');}
    updateProgress(job.progress);state.poll=setTimeout(pollJob,1100);
  }catch(err){clearInterval(state.timer);if(err.status===404){state.jobId=null;sessionStorage.removeItem('bozhi-active-job');}
    $('#progress-title').textContent=state.jobId?'连接暂时中断了。':'这次整理暂时无法继续。';
    $('#progress-description').textContent=err.message+(state.jobId?' 后台任务可能仍在继续，你可以重新连接或查看处理记录。':' 你可以查看处理记录，或重新开始一批单据。');
    $('#progress-section').classList.add('failed');$('#reconnect').hidden=!state.jobId;
    // Keep the job id for explicit reconnect; never relaunch an expensive job automatically.
    setBusy(false);}}
async function submit(e){e.preventDefault();if(state.busy||!state.files.length)return;
  if(state.jobId){toast('上一批任务还未确认完成，请先重新连接或查看处理记录。',true);return;}
  let urls;try{urls=JSON.parse($('#urls-json').value);if(!urls||Array.isArray(urls)||typeof urls!=='object'||Object.values(urls).some(v=>typeof v!=='string'))throw Error();}
  catch(_){toast('文件访问清单格式不正确，请在连接设置中检查。',true);showDialog('#settings-dialog');return;}
  setBusy(true);$('#result').hidden=true;const panel=$('#progress-section');panel.hidden=false;panel.classList.remove('failed','completed');
  updateProgress({stage:'queued'});$('#progress-title').textContent='正在接收你选择的文件。';$('#progress-description').textContent='上传完成后，我会开始读取和核对。';
  const data=new FormData();state.files.forEach(f=>data.append('files',f));data.append('entity',$('#entity').value.trim());
  data.append('existing_json',state.existing);data.append('urls_json',JSON.stringify(urls));
  try{const job=await(await request('/api/jobs',{method:'POST',body:data})).json();state.jobId=job.job_id;
    sessionStorage.setItem('bozhi-active-job',job.job_id);startClock(job.created_at);await pollJob();
  }catch(err){setBusy(false);$('#progress-section').classList.add('failed');$('#progress-title').textContent='还没有开始整理。';
    $('#progress-description').textContent=err.message;toast(err.message,true);}}
function sourceNames(record){return(state.report?.sources||[]).filter(s=>record.audit_trail.sources.includes(s.source_id)).map(s=>s.filename);}
function money(value){if(value==null)return '待确认';const text=String(value);if(/^-?\d+(\.\d+)?$/.test(text)){
  const [whole,part='']=text.split('.');return whole.replace(/\B(?=(\d{3})+(?!\d))/g,',')+'.'+part.padEnd(2,'0');}
  return new Intl.NumberFormat('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:6}).format(value);}
function amountFor(record){return state.report?.display_summary?.line_amounts[state.report.records.indexOf(record)]??record.charge_details.amount;}
function metric(label,value,unit=''){const card=el('div','metric');const v=el('div','metric-value',value);if(unit)v.append(el('small','',unit));card.append(el('span','metric-label',label),v);return card;}
function renderReport(report,fromJob=false){state.report=report;state.filter='ALL';$('#search').value='';$('#result').hidden=false;navigate('work');
  $('#progress-section').hidden=!fromJob;$('#result .eyebrow').textContent=fromJob?'本次整理结果':'历史整理结果';
  const fees=report.records.filter(r=>r.charge_details.amount!=null);const pending=report.records.filter(r=>r.operation==='PENDING_REVIEW').length;
  const roots=report.sources.filter(s=>!s.parent_id).length;
  $('#result-summary').textContent=`共读取 ${roots} 个文件和 ${report.sources.length-roots} 个附件，整理出 ${fees.length} 条有金额的费用记录。${pending?`已标出 ${pending} 条待确认记录。`:'你可以逐条查看依据，再导出整理结果。'}`;
  const notice=$('#result-notice');const flags=new Set(report.records.flatMap(r=>r.audit_trail.issues));let notes=[];
  if(report.metadata.extraction_mode==='replay')notes.push('这是一次保存结果的重放，没有发起新的模型识别。');
  if(flags.has('LOCAL_PREVIEW_REQUIRES_EXAM_OCR'))notes.push('部分文件采用本地预览解析，仍需要通过指定文档服务复核。');
  if(flags.has('OCR_PUBLIC_URL_REQUIRED'))notes.push('文档服务尚未取得部分文件的下载地址。请在连接设置中补充文件访问清单后重试。');
  if(!report.metadata.accounting_entity)notes.push('当前记账公司尚未确认，应收应付方向暂不作为结论。');
  notice.hidden=!notes.length;notice.textContent=notes.join(' ');
  const metrics=$('#metrics');metrics.replaceChildren(metric('费用条目',fees.length,'笔'),metric('需要确认',pending,'条'),metric('涉及来源',report.sources.length,'份'));
  const amount=metric('费用金额（含待确认）','');amount.classList.add('amount-metric');const v=amount.querySelector('.metric-value');
  const totals=new Map((report.display_summary?.totals||[]).map(t=>[t.currency,t.amount]));
  if(!totals.size)v.textContent='暂无可汇总金额';for(const[c,n]of totals)v.append(el('div','',`${c} ${money(n)}`));metrics.append(amount);
  renderFilters();renderRows();$('#raw-report').textContent=JSON.stringify(report,null,2);
  $('#result').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth',block:'start'});}
function renderFilters(){const root=$('#filters');root.replaceChildren();const choices=[['ALL','全部'],['PENDING_REVIEW','待确认'],['CREATE','新增'],['UPDATE','更新'],['NO_CHANGE','未变化']];
  for(const[key,label]of choices){const count=state.report.records.filter(r=>key==='ALL'||r.operation===key).length;const button=el('button','filter-tab'+(state.filter===key?' active':''),label);
    button.type='button';button.setAttribute('aria-pressed',String(state.filter===key));button.append(el('span','',count));
    button.addEventListener('click',()=>{state.filter=key;renderFilters();renderRows();});root.append(button);}}
function renderRows(){if(!state.report)return;const query=$('#search').value.trim().toLocaleLowerCase();const body=$('#rows');body.replaceChildren();let visible=0;
  state.report.records.forEach((r,index)=>{if(state.filter!=='ALL'&&r.operation!==state.filter)return;
    const haystack=[r.charge_details.charge_item,r.document_info.reference_no,r.document_info.bill_of_lading,...sourceNames(r)].join(' ').toLocaleLowerCase();
    if(query&&!haystack.includes(query))return;visible++;
    const row=el('tr');const name=el('td');const title=el('div','row-title',r.charge_details.charge_item||'未提取到实际费用');title.title=title.textContent;
    const meta=el('div','row-meta',r.document_info.reference_no||r.document_info.bill_of_lading||sourceNames(r)[0]||'单据号待确认');meta.title=sourceNames(r).join(' · ');name.append(title,meta);
    const amount=el('td','amount-cell',money(amountFor(r)));if(r.charge_details.currency)amount.append(el('small','',r.charge_details.currency));
    const op=el('td');op.append(el('span','status-pill '+({PENDING_REVIEW:'pending',UPDATE:'update',NO_CHANGE:'unchanged'}[r.operation]||''),operations[r.operation]||r.operation));
    const direction=el('td','direction-cell',r.financial_attributes.posting_type==='AR'?'应收':r.financial_attributes.posting_type==='AP'?'应付':'待确认');
    const detail=el('td');const button=el('button','table-button','查看依据');button.type='button';button.setAttribute('aria-label',`查看第 ${index+1} 条记录的依据`);
    button.addEventListener('click',()=>openRecord(r));detail.append(button);row.append(name,amount,op,direction,detail);body.append(row);});
  $('#empty-results').hidden=visible!==0;$('#visible-count').textContent=`显示 ${visible} / ${state.report.records.length} 条记录`;}
function detailField(label,value){const field=el('div','detail-field');field.append(el('label','',label),el('p','',value==null||value===''?'待确认':value));return field;}
function openRecord(r){$('#record-title').textContent=r.charge_details.charge_item||'需要确认的材料';const content=$('#record-content');content.replaceChildren();
  const grid=el('div','detail-grid');grid.append(detailField('处理建议',operations[r.operation]),detailField('金额',r.charge_details.amount==null?null:`${money(amountFor(r))} ${r.charge_details.currency||'币种待确认'}`),
    detailField('单据编号 / 提单号',r.document_info.reference_no||r.document_info.bill_of_lading),detailField('当前记账公司',r.financial_attributes.accounting_entity),
    detailField('收付方向',r.financial_attributes.posting_type==='AR'?'应收':r.financial_attributes.posting_type==='AP'?'应付':null),detailField('文件来源',sourceNames(r).join('、')));
  content.append(grid);if(r.audit_trail.issues.length){content.append(el('h3','detail-heading','这些地方，需要你再看一眼'));const list=el('ul','issue-list');
    for(const issue of r.audit_trail.issues)list.append(el('li','',explain(issue)));content.append(list);}
  if(r.audit_trail.changed_fields.length){content.append(el('h3','detail-heading','明确提出的字段变更'));
    for(const path of r.audit_trail.changed_fields){const[group,key]=path.split('.');content.append(el('p','detail-text',`${fields[path]||path}：${r.audit_trail.previous_values[path]??'空'} → ${r[group]?.[key]??'空'}`));}}
  content.append(el('h3','detail-heading','我依据了这些内容'));
  if(!r.audit_trail.evidence.length)content.append(el('p','detail-text','暂时没有可核查的字段证据。请打开原始文件确认。'));
  for(const e of r.audit_trail.evidence){const card=el('div','evidence-card');const source=state.report.sources.find(s=>s.source_id===e.source_id);
    card.append(el('div','evidence-label',`${fields[e.path]||e.path} · ${source?.filename||'来源待确认'}`),el('blockquote','',e.quote));
    if(source?.method==='exam-ocr-api-structured')card.append(el('small','','此证据来自文档服务的结构化响应，尚需对照原件。'));
    else card.append(el('small','',`提取文本中的字符位置 ${e.start}–${e.end}`));content.append(card);}
  if(r.audit_trail.ai_reasoning.length){content.append(el('h3','detail-heading','处理说明'));for(const text of r.audit_trail.ai_reasoning)content.append(el('p','detail-text',text));}
  showDialog('#record-dialog');}
async function exportReport(format){if(!state.report)return;try{const response=await request(`/api/runs/${state.report.run_id}/export?format=${format}`);
  const blob=await response.blob();const url=URL.createObjectURL(blob);const a=el('a');a.href=url;a.download=`泊知-账单-${state.report.run_id.slice(0,8)}.${format}`;
  document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),20000);toast('已为你准备好导出文件。');
}catch(err){toast(err.message,true);}}
async function loadHistory(){const list=$('#history-list');list.replaceChildren(el('div','panel empty-message','正在查找之前的整理记录…'));
  try{const data=await(await request('/api/runs')).json();list.replaceChildren();if(!data.items.length){list.append(el('div','panel empty-message','还没有整理记录。上传第一份单据后，结果会保存在这里。'));return;}
    for(const item of data.items){const button=el('button','history-item');const badge=el('span','history-icon');badge.append(icon('file'));
      const copy=el('div','history-copy');const title=el('p','history-title',item.filenames.join('、')||'未命名批次');title.title=title.textContent;
      const stamp=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}).format(new Date(item.created_at*1000));
      copy.append(title,el('p','history-meta',`${stamp} · ${item.filenames.length} 个文件${item.metadata.extraction_mode==='replay'?' · 保存结果重放':''}`));
      button.append(badge,copy,el('span','history-stats',`${item.metadata.record_count} 条记录 · ${item.metadata.pending_count} 条待确认`),icon('arrow'));
      button.addEventListener('click',async()=>{try{const r=await(await request('/api/runs/'+item.run_id)).json();renderReport(r);}catch(err){toast(err.message,true);}});list.append(button);}
  }catch(err){list.replaceChildren(el('div','panel empty-message',err.message));}}
function resetBatch(){if(state.busy)return;if(state.jobId){toast('请先确认上一批任务是否已完成，再开始新一批。',true);return;}
  state.files=[];state.report=null;state.existing='[]';$('#existing-file').value='';$('#existing-info').hidden=true;$('#result').hidden=true;
  $('#progress-section').hidden=true;$('#existing-details').open=false;renderFiles();navigate('work');window.scrollTo({top:0,behavior:'smooth'});toast('准备好了，请把新一批单据发给我。');}

$('#choose-files').addEventListener('click',e=>{e.stopPropagation();if(!state.busy)$('#files').click();});
$('#dropzone').addEventListener('click',()=>{if(!state.busy)$('#files').click();});
$('#dropzone').addEventListener('keydown',e=>{if(e.target===e.currentTarget&&(e.key==='Enter'||e.key===' ')){e.preventDefault();if(!state.busy)$('#files').click();}});
$('#files').addEventListener('change',e=>{addFiles([...e.target.files]);e.target.value='';});
for(const event of ['dragover','dragenter'])$('#dropzone').addEventListener(event,e=>{e.preventDefault();if(!state.busy)$('#dropzone').classList.add('dragging');});
for(const event of ['dragleave','drop'])$('#dropzone').addEventListener(event,e=>{e.preventDefault();$('#dropzone').classList.remove('dragging');});
$('#dropzone').addEventListener('drop',e=>addFiles([...e.dataTransfer.files]));
$('#clear-files').addEventListener('click',()=>{state.files=[];renderFiles();});$('#form').addEventListener('submit',submit);
$('#new-batch').addEventListener('click',resetBatch);$('#nav-work').addEventListener('click',()=>navigate('work'));
$('#nav-history').addEventListener('click',()=>navigate('history'));$('#refresh-history').addEventListener('click',loadHistory);
for(const id of ['#help-open','#quick-help'])$(id).addEventListener('click',()=>showDialog('#help-dialog'));
$('#settings-open').addEventListener('click',()=>showDialog('#settings-dialog'));
document.querySelectorAll('.dialog-close').forEach(b=>b.addEventListener('click',()=>b.closest('dialog').close()));
document.querySelectorAll('dialog').forEach(d=>d.addEventListener('click',e=>{if(e.target!==d)return;const r=d.getBoundingClientRect();
  if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)d.close();}));
$('#save-settings').addEventListener('click',async()=>{try{const data=JSON.parse($('#urls-json').value);if(!data||Array.isArray(data)||typeof data!=='object'||Object.values(data).some(v=>typeof v!=='string'))throw Error();}
  catch(_){toast('文件访问清单需要是“文件 SHA256: 下载地址”的 JSON 对象。',true);return;}
  state.token=$('#api-token').value.trim();$('#settings-dialog').close();await connect();});
$('#existing-file').addEventListener('change',async e=>{const file=e.target.files[0];if(!file)return;
  try{if(file.size>state.limits.max_file_bytes)throw new Error('旧账单文件超过大小上限。');const text=await file.text();const parsed=JSON.parse(text.replace(/^\uFEFF/,''));
    if(!Array.isArray(parsed))throw new Error('旧账单需要是标准 JSON 数组。');state.existing=text.replace(/^\uFEFF/,'');
    $('#existing-info span').textContent=`${file.name} · ${parsed.length} 条账单`;$('#existing-info').hidden=false;toast('已收到旧账单，整理时会一起对照。');
  }catch(err){e.target.value='';toast(err.message||'旧账单读取失败，请检查文件。',true);}});
$('#remove-existing').addEventListener('click',()=>{state.existing='[]';$('#existing-file').value='';$('#existing-info').hidden=true;});
$('#search').addEventListener('input',renderRows);$('#export-json').addEventListener('click',()=>exportReport('json'));
$('#export-csv').addEventListener('click',()=>exportReport('csv'));$('#report-open').addEventListener('click',()=>showDialog('#report-dialog'));
$('#reconnect').addEventListener('click',()=>{if(!state.jobId)return;$('#progress-section').classList.remove('failed');setBusy(true);startClock(Date.now()/1000);pollJob();});
connect();
const previousJob=sessionStorage.getItem('bozhi-active-job');if(previousJob){state.jobId=previousJob;$('#progress-section').hidden=false;setBusy(true);pollJob();}
