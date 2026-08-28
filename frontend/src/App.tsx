import { BrowserRouter, Route, Routes } from "react-router-dom";
import styles from "./App.module.css";
import { Spinner } from "./components/Spinner";
import { useAuth } from "./hooks/useAuth";
import { DashboardPage } from "./pages/DashboardPage";
import { LandingPage } from "./pages/LandingPage";
import { RepositoryDetailPage } from "./pages/RepositoryDetailPage";

export function App() {
  const { user, loading, clear } = useAuth();

  if (loading) {
    return (
      <div className={styles.loadingScreen}>
        <Spinner label="Loading" />
      </div>
    );
  }

  return (
    <BrowserRouter>
      <Routes>
        <Route
          path="/"
          element={user ? <DashboardPage user={user} onLogout={clear} /> : <LandingPage />}
        />
        <Route
          path="/repositories/:id"
          element={user ? <RepositoryDetailPage /> : <LandingPage />}
        />
      </Routes>
    </BrowserRouter>
  );
}
