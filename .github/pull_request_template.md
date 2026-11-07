## Qué cambia

<!-- Una o dos frases. -->

## Cómo se probó

- [ ] `ruff`, `mypy` y `pytest` (cobertura mínima de 80 %) pasan: la CI lo comprueba.
- [ ] Si cambia el chatbot, probé la conversación (pruebas del orquestador o WhatsApp real).
- [ ] Si hay migración, corre con `alembic upgrade head` y tiene `downgrade`.

## Revisión humana (I-17)

- [ ] Una persona del equipo leyó el código completo, también el que generó un agente de IA, y lo aprueba en este PR.
- [ ] No hay secretos, claves ni datos personales (teléfonos sin enmascarar) en el código, los logs ni los ejemplos.
