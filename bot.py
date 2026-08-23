def inicializar_bd():
    conn = get_db_connection()
    if not conn:
        print("⚠️ Advertencia: No hay DATABASE_URL configurada.", flush=True)
        return
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS rifa_estado (
                id INT PRIMARY KEY,
                data JSONB
            );
        """)
        cur.execute("SELECT data FROM rifa_estado WHERE id = 1;")
        row = cur.fetchone()
        if not row:
            data_inicial = {
                "estado_rifa": "activa",
                "numeros": {str(i): {"estado": "disponible", "nombre": "", "user_id": "", "username": ""} for i in range(1, 101)},
                "solicitudes_pendientes": {},
                "idiomas_usuarios": {}
            }
            cur.execute("INSERT INTO rifa_estado (id, data) VALUES (1, %s);", (json.dumps(data_inicial),))
            conn.commit()
        cur.close()
        conn.close()
        print("🗄️ Base de datos PostgreSQL inicializada correctamente en Render.", flush=True)
    except Exception as e:
        print(f"⚠️ Error menor al conectar con la BD (el bot seguirá vivo): {e}", flush=True)

if __name__ == "__main__":
    print("🚀 Iniciando servidor Flask...", flush=True)
    inicializar_bd()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
