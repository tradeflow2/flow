import sys
sys.path.insert(0, 'api')
try:
    from tests import support
    print('support imported OK')
    app = support.make_app()
    print('app created OK')
    print('total routes:', len(app.routes))
    for r in app.routes:
        p = getattr(r, 'path', '')
        if 'payment' in p or 'pre-invoice' in p:
            print('PAYMENT ROUTE:', p)
except Exception as e:
    import traceback
    traceback.print_exc()
