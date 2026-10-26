// 服务启动入口：创建应用并监听端口。
import { createApp } from "./app.js";

const PORT = Number(process.env.PORT ?? 3001);

createApp().listen(PORT, () => {
  console.log(`login_demo listening on http://localhost:${PORT}`);
});
