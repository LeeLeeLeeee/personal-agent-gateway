import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { GatewayApp } from "./components/containers/GatewayApp/index.jsx";
import "../../src/personal_agent_gateway/static/styles.css";

createRoot(document.getElementById("app")).render(
  <StrictMode>
    <GatewayApp />
  </StrictMode>
);
