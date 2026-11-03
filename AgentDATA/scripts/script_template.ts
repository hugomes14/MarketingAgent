type ScriptResult = {
  status: "ok" | "error";
  message: string;
};

function runScript(): ScriptResult {
  return {
    status: "ok",
    message: "Template de script TypeScript."
  };
}

const result = runScript();
console.log(JSON.stringify(result, null, 2));
