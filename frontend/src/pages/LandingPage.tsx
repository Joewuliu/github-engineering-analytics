import styles from "./LandingPage.module.css";

// The login link is a real browser navigation, deliberately not a fetch
// call -- GitHub OAuth is a redirect flow the frontend must never intercept
// or wrap, and it never sees or handles any token.
export function LandingPage() {
  return (
    <main className={styles.main}>
      <div className={styles.content}>
        <h1 className={styles.title}>GitHub Engineering Analytics</h1>
        <p className={styles.subtitle}>
          Track repositories, ingest pull request activity, and see engineering metrics computed
          from real GitHub data &mdash; a backend built with FastAPI, PostgreSQL, and a Dramatiq
          background worker.
        </p>
        <a className={styles.signIn} href="/auth/github/login">
          Sign in with GitHub
        </a>
      </div>
    </main>
  );
}
