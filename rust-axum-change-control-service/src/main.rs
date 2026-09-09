use determa_change_control::{open_state, router};
use std::{env, net::SocketAddr, path::PathBuf};
use tokio::net::TcpListener;
use tracing_subscriber::EnvFilter;

const DEFAULT_ADDRESS: &str = "127.0.0.1:8080";

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()))
        .init();
    let database = absolute_database_path()?;
    if let Some(parent) = database.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let state = open_state(&database)?;
    let address: SocketAddr = env::var("CHANGE_CONTROL_ADDRESS")
        .unwrap_or_else(|_| DEFAULT_ADDRESS.into())
        .parse()?;
    let listener = TcpListener::bind(address).await?;
    tracing::info!(%address, database = %database.display(), "change-control service listening");
    axum::serve(listener, router(state))
        .with_graceful_shutdown(shutdown())
        .await?;
    Ok(())
}

fn absolute_database_path() -> Result<PathBuf, Box<dyn std::error::Error>> {
    let configured = env::var_os("CHANGE_CONTROL_DATABASE")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("data/change-control.sqlite3"));
    if configured.is_absolute() {
        Ok(configured)
    } else {
        Ok(env::current_dir()?.join(configured))
    }
}

async fn shutdown() {
    let _ = tokio::signal::ctrl_c().await;
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_address_is_loopback_only() {
        let address: SocketAddr = DEFAULT_ADDRESS.parse().expect("valid default address");

        assert!(address.ip().is_loopback());
        assert_eq!(address.port(), 8080);
    }
}
