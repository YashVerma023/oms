// Choosing a file in Data Operation: CSV goes straight to the preview, a
// workbook offers its sheets.
//   python tests/render_page.py /admin/data-ops /tmp/dops.html
//   node   tests/test_sheet_picker.js /tmp/dops.html
const fs=require("fs"),path=require("path"),{JSDOM}=require("jsdom");
const ROOT="/sessions/wizardly-loving-mayer/mnt/omp";
const CSV_COLS={columns:[{name:"a",source:"A",type:"text",samples:["1"]}],rows:2,sheet:""};
const SHEETS={sheets:["Main","Jainam","Category"]};
const SHEET_COLS={columns:[{name:"x",source:"X",type:"text",samples:["9"]}],rows:5,sheet:"Main"};
const HTML=process.argv[2]||"/tmp/dops.html";
const dom=new JSDOM(fs.readFileSync(HTML,"utf8"),{runScripts:"outside-only",pretendToBeVisual:true});
const {window}=dom;
let calls=[]; let picked="";
window.fetch=(url,opts)=>{
  const sheet=(opts&&opts.body&&opts.body.get)?opts.body.get("sheet"):null;
  const name=picked;   // FormData cannot carry a fake File in jsdom
  calls.push({url,sheet,name});
  if(url.indexOf("inspect")===-1) return Promise.resolve({ok:true,json:()=>Promise.resolve({})});
  if(String(name).endsWith(".csv")) return Promise.resolve({ok:true,json:()=>Promise.resolve(CSV_COLS)});
  if(!sheet) return Promise.resolve({ok:true,json:()=>Promise.resolve(SHEETS)});
  return Promise.resolve({ok:true,json:()=>Promise.resolve(Object.assign({},SHEET_COLS,{sheet}))});
};
const conf=fs.readFileSync(HTML,"utf8").match(/window\.OMP_DATA_OPS\s*=\s*\{[\s\S]*?\};/);
window.eval(conf[0]);
["static/js/filters.js","static/js/data-ops.js"].forEach(f=>window.eval(fs.readFileSync(path.join(ROOT,f),"utf8")));
const $=id=>window.document.getElementById(id);
let bad=0;
const check=(l,g,w)=>{const ok=JSON.stringify(g)===JSON.stringify(w);
  console.log((ok?"PASS  ":"FAIL  ")+l+" -> "+JSON.stringify(g)+(ok?"":"  want "+JSON.stringify(w)));if(!ok)bad++;};
const pick=(name)=>{
  picked=name;
  const input=$("sheetFile");
  Object.defineProperty(input,"files",{value:[{name}],configurable:true});
  input.dispatchEvent(new window.Event("change",{bubbles:true}));
};

check("picker hidden before any file", $("sheetPickWrap").hidden, true);

pick("all-users.csv");
setTimeout(()=>{
  check("CSV: picker stays hidden", $("sheetPickWrap").hidden, true);
  check("CSV: read automatically, no button press", calls.filter(c=>c.url.indexOf("inspect")!==-1).length, 1);
  check("CSV: columns previewed", $("previewBlock").hidden, false);
  check("CSV: table name suggested", $("sheetTable").value, "all_users");

  calls=[];
  pick("All User 21AUG26.xlsx");
  setTimeout(()=>{
    check("Excel: picker shown", $("sheetPickWrap").hidden, false);
    check("Excel: every sheet offered", [...$("sheetPick").options].map(o=>o.value), ["Main","Jainam","Category"]);
    check("Excel: first sheet preselected", $("sheetPick").value, "Main");
    check("Excel: told to press View", $("sheetNote").textContent.indexOf("View")!==-1, true);
    check("Excel: nothing previewed until View", $("previewBlock").hidden, true);

    $("sheetPick").value="Category";
    $("sheetPick").dispatchEvent(new window.Event("change",{bubbles:true}));
    $("btnViewSchema").click();
    setTimeout(()=>{
      const last=calls[calls.length-1];
      check("View reads the chosen sheet", last.sheet, "Category");
      check("preview follows the chosen sheet",
        $("previewSummary").textContent.indexOf("'Category'")!==-1, true);
      console.log(bad?`\n${bad} FAILED`:"\nSheet selection works for both file types");
      process.exit(bad?1:0);
    },30);
  },30);
},30);
