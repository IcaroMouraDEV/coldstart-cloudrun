import httpx

AUTH_GW = {"Authorization": "Bearer token-do-gateway", "X-Forwarded-Authorization": "Bearer valido"}


def test_so_a_primeira_requisicao_e_fria(backend):
    _, cliente = backend
    respostas = [cliente.get("/posts") for _ in range(3)]
    assert [r.headers["X-Cold-Start"] for r in respostas] == ["1", "0", "0"]
    assert len({r.headers["X-Instance-Id"] for r in respostas}) == 1
    idades = [int(r.headers["X-Instance-Age-Ms"]) for r in respostas]
    assert idades == sorted(idades) and idades[0] >= 0


def test_cada_instancia_tem_sua_primeira_requisicao(auth, backend):
    assert auth[1].get("/health").headers["X-Cold-Start"] == "1"
    assert backend[1].get("/health").headers["X-Cold-Start"] == "1"


def test_verify_usa_token_encaminhado_pelo_gateway(auth):
    _, cliente = auth
    assert cliente.get("/auth/verify", headers=AUTH_GW).json() == {"uid": "u1", "email": "u1@teste.com"}


def test_verify_aceita_authorization_direto(auth):
    _, cliente = auth
    assert cliente.get("/auth/verify", headers={"Authorization": "Bearer valido"}).status_code == 200


def test_verify_rejeita_token_ausente_ou_invalido(auth):
    _, cliente = auth
    assert cliente.get("/auth/verify").status_code == 401
    assert cliente.get("/auth/verify", headers={"Authorization": "Bearer ruim"}).status_code == 401


def test_login_repassa_resposta_do_firebase(auth):
    modulo, cliente = auth

    def firebase(request):
        assert request.url.params["key"] == "teste"
        return httpx.Response(200, json={
            "idToken": "id", "refreshToken": "rf", "expiresIn": "3600", "localId": "u1",
        })

    modulo.cliente_http = httpx.AsyncClient(transport=httpx.MockTransport(firebase))
    resposta = cliente.post("/auth/login", json={"email": "a@b.com", "senha": "x"})
    assert resposta.json() == {"id_token": "id", "refresh_token": "rf", "expira_em": 3600, "uid": "u1"}


def test_login_com_credenciais_invalidas(auth):
    modulo, cliente = auth
    modulo.cliente_http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(400)))
    assert cliente.post("/auth/login", json={"email": "a@b.com", "senha": "x"}).status_code == 401


def test_crud_de_posts(backend):
    _, cliente = backend
    novo = {"titulo": "t", "conteudo": "c"}

    assert cliente.post("/posts", json=novo).status_code == 401
    post_id = cliente.post("/posts", json=novo, headers=AUTH_GW).json()["id"]

    assert cliente.get(f"/posts/{post_id}").json()["autor"] == "u1"
    assert [p["id"] for p in cliente.get("/posts").json()] == [post_id]

    alterado = {"titulo": "t2", "conteudo": "c2"}
    assert cliente.put(f"/posts/{post_id}", json=alterado, headers=AUTH_GW).status_code == 200
    assert cliente.get(f"/posts/{post_id}").json()["titulo"] == "t2"

    assert cliente.delete(f"/posts/{post_id}", headers=AUTH_GW).status_code == 204
    assert cliente.get(f"/posts/{post_id}").status_code == 404
    assert cliente.put(f"/posts/{post_id}", json=alterado, headers=AUTH_GW).status_code == 404


def test_listagem_ordena_do_mais_recente(backend):
    modulo, cliente = backend
    for i in range(25):
        modulo.colecao.document(f"p{i:02d}").set({"titulo": "t", "conteudo": "c", "criado_em": i})
    ids = [p["id"] for p in cliente.get("/posts").json()]
    assert len(ids) == 20 and ids[0] == "p24"
