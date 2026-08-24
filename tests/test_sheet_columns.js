// Choosing a sheet, viewing its schema, and picking which columns to keep.
//   python tests/render_page.py /admin/data-ops /tmp/dops.html
//   node   tests/test_sheet_columns.js /tmp/dops.html
const fs=require("fs"),path=require("path"),{JSDOM}=require("jsdom");
const ROOT="/sessions/wizardly-loving-mayer/mnt/omp";
const SHEETS={sheets:["Main","Jainam","Category"]};
const COLS=(sheet)=>({sheet,rows:807,columns:[
 {index:0,name:"userId",source:"userId",type:"text",samples:["AB1"]},
 {index:1,name:"alias",source:"alias",type:"text",samples:["MSR_X_2C"]},
 {index:2,name:"secret",source:"secret",type:"text",samples:["hush"]},
 {index:3,name:"allocation",source:"allocation",type:"int",samples:["200000"]}]});
const HTML=process.argv[2]||"/tmp/dops.html";
const dom=new JSDOM(fs.readFileSync(HTML,"utf8"),{runScripts:"outside-only",pretendToBeVisual:true});
const {window}=dom;
let picked="", lastCreate=null, calls=[];
window.fetch=(url,opts)=>{
  const body=opts&&opts.body; const sheet=body&&body.get?body.get("sheet"):null;
  calls.push({url,sheet});
  if(url.indexOf("inspect")!==-1){
    if(picked.endsWith(".xlsx") && !sheet) return Promise.resolve({ok:true,json:()=>Promise.resolve(SHEETS)});
    return Promise.resolve({ok:true,json:()=>Promise.resolve(COLS(sheet||""))});
  }
  lastCreate=JSON.parse(body.get("columns"));
  return Promise.resolve({ok:true,json:()=>Promise.resolve(
    {table:"t",columns:lastCreate.length,keys:[],loaded:807,skipped:0,ddl:"CREATE ..."})});
};
const conf=fs.readFileSync(HTML,"utf8").match(/window\.OMP_DATA_OPS\s*=\s*\{[\s\S]*?\};/);
window.eval(conf[0]);
["static/js/filters.js","static/js/data-ops.js"].forEach(f=>window.eval(fs.readFileSync(path.join(ROOT,f),"utf8")));
const $=id=>window.document.getElementById(id);
let bad=0;
const check=(l,g,w)=>{const ok=JSON.stringify(g)===JSON.stringify(w);
  console.log((ok?"PASS  ":"FAIL  ")+l+" -> "+JSON.stringify(g)+(ok?"":"  want "+JSON.stringify(w)));if(!ok)bad++;};
const pickFile=(name)=>{picked=name;const i=$("sheetFile");
  Object.defineProperty(i,"files",{value:[{name}],configurable:true});
  i.dispatchEvent(new window.Event("change",{bubbles:true}));};

pickFile("All User 21AUG26.xlsx");
setTimeout(()=>{
  check("sheets auto-loaded on upload", [...$("sheetPick").options].map(o=>o.value), ["Main","Jainam","Category"]);
  check("View button is there", !$("sheetPickWrap").hidden, true);
  check("no schema shown until View", $("previewBlock").hidden, true);

  $("sheetPick").value="Jainam";
  $("sheetPick").dispatchEvent(new window.Event("change",{bubbles:true}));
  check("changing sheet does not auto-load", $("previewBlock").hidden, true);

  $("btnViewSchema").click();
  setTimeout(()=>{
    check("View loads that sheet's schema", calls[calls.length-1].sheet, "Jainam");
    check("schema shown", $("previewBlock").hidden, false);
    check("four columns", $("previewBody").rows.length, 4);
    check("all ticked by default",
      [...$("previewBody").querySelectorAll('input[data-role="include"]')].every(b=>b.checked), true);
    check("button counts them", $("btnCreateSheet").textContent, "Create table with 4 column(s)");

    // Untick 'secret' - the column in the MIDDLE, the one that would shift data.
    const boxes=[...$("previewBody").querySelectorAll('input[data-role="include"]')];
    boxes[2].checked=false;
    boxes[2].dispatchEvent(new window.Event("change",{bubbles:true}));
    check("count updates", $("btnCreateSheet").textContent, "Create table with 3 column(s)");
    check("excluded row is marked", $("previewBody").rows[2].className, "excluded");

    $("previewBody").rows[0].querySelector('input[data-role="key"]').checked=true;
    $("btnCreateSheet").click();
    setTimeout(()=>{
      check("only ticked columns sent", lastCreate.map(c=>c.name), ["userId","alias","allocation"]);
      check("each carries its SHEET position, not its new one",
        lastCreate.map(c=>c.index), [0,1,3]);
      check("key travels too", lastCreate.map(c=>c.key), [true,false,false]);

      // select-all restores everything
      $("btnViewSchema").click();
      setTimeout(()=>{
        const all=$("includeAll"); all.checked=false;
        all.dispatchEvent(new window.Event("change",{bubbles:true}));
        check("untick all disables Create", $("btnCreateSheet").disabled, true);
        all.checked=true; all.dispatchEvent(new window.Event("change",{bubbles:true}));
        check("tick all restores", $("btnCreateSheet").textContent, "Create table with 4 column(s)");
        console.log(bad?`\n${bad} FAILED`:"\nSheet -> View -> pick columns works");
        process.exit(bad?1:0);
      },30);
    },30);
  },30);
},30);
