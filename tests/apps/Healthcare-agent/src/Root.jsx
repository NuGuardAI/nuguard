import { BrowserRouter as Router, Routes, Route } from "react-router-dom";
import App from "./App";                     
import Dashboard from "./components/Dashboard";  
import Assistant from "./components/Assistant";
import UserContext from "./context/UserContext";
import Recommendation from "./components/Recommendation";
import Success from "./components/Success";

const Root = () => {
  return (
    <Router>
      <UserContext>
        <Routes>
          <Route path="/" element={<App />} />
          <Route path="/login" element={<App />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/assistant" element={<Assistant />} />
          <Route path="/recommendation" element={<Recommendation />} />
          <Route path="/success" element={<Success />} />
        </Routes>
      </UserContext>
    </Router>
  );
};

export default Root;
