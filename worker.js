const http = require('http');
const { exec } = require('child_process');

const port = process.env.PORT || 8080;

// health check
function health(req, res) {
  res.writeHead(200, {'Content-Type': 'application/json'});
  res.end(JSON.stringify({status:"ok", worker:"oppo-cph2699", ip:"100.120.93.12"}));
}

// handle tasks
function task(req, res) {
  let body="";
  req.on("data", chunk => body+=chunk);
  req.on("end", () => {
    try {
      const data = JSON.parse(body||"{}");
      if (data.cmd) {
        exec(data.cmd, (err, out, errout)=>{
          res.writeHead(200, {'Content-Type':'application/json'});
          res.end(JSON.stringify({ok:!err, out, err:errout}));
        });
      } else {
        res.writeHead(400); res.end("No cmd");
      }
    } catch(e){ res.writeHead(500); res.end(e.toString()); }
  });
}

http.createServer((req, res)=>{
  if (req.url==="/health") return health(req,res);
  if (req.url==="/task" && req.method==="POST") return task(req,res);
  res.writeHead(404); res.end("Not found");
}).listen(port, ()=>console.log("Worker running on", port));
