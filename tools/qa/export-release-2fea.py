import base64,io,json,tarfile
from pathlib import Path
from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding,pkcs7
CERT='-----BEGIN CERTIFICATE-----\nMIIEKzCCApOgAwIBAgIUfTbqbVGhUi9LiUVI3dklFgaya1MwDQYJKoZIhvcNAQEL\nBQAwJTEjMCEGA1UEAwwaeWxldW0tcWEtZXZpZGVuY2UtMjAyNjEwMDEwHhcNMjYx\nMDAxMTQyMzQ1WhcNMjYxMDA0MTQyMzQ1WjAlMSMwIQYDVQQDDBp5bGV1bS1xYS1l\ndmlkZW5jZS0yMDI2MTAwMTCCAaIwDQYJKoZIhvcNAQEBBQADggGPADCCAYoCggGB\nAM7pocztzKgwhU6ulfoL95rE0YGAxEumWlh2tWobLgp98W4egPcr61gop0+sHpN5\n/5eKaGkedN0JtylPGKx720LNYmvThdS3sydP2tP79ebTsyESCMX6YOw5xhO5Voao\nGP6UtoRUjXYvSITsetpvBoBkg/PBG5nEXrSi5dhCYFJqjI1FdEo5pOeoYxxeSOAc\n8IkM89ds8uLVhY3GJjHtnWL5xGsQntvIasGDAYbrdFCpb61aunDUKLmBwyAxaKBy\nu1tF6a8cnF7BiszSimTEpYn5+LtKxxLey8AvfMRPwxrDpTyVtjuuEmHB6eaMvnft\n5FY3Dpac69ONkvBQq1wePMeClMG0d9y5WW8mOijz3Wp+PtuZiJ8a1V7uVofaFyIc\nypM5d2jtR7PAJnrsZHwzsT8YwfR6gDXKv7JsdPtpW6JHgpJxe24bFjfdY160J++C\ntEv/wwTilC2kOj1iWxKzxnZxLqHTshmTo2Iy5VqInUndEwGA4kN7uvlfK6PrWT8l\nnwIDAQABo1MwUTAdBgNVHQ4EFgQUtZi6w+/jyHMNZPnMCFU5KL4jkMwwHwYDVR0j\nBBgwFoAUtZi6w+/jyHMNZPnMCFU5KL4jkMwwDwYDVR0TAQH/BAUwAwEB/zANBgkq\nhkiG9w0BAQsFAAOCAYEAlhsjchTXNRDhvtEjdxWhfTDF1Dy/3xi+YOgjcebgXWca\n8eq0kLNdcsl5AqZjfU6FpTlt474xo/OiJHNLUvpUo4rtvlHBtYPAXmrRK/2QC/5P\nbT0HrAdqCtkvsz3VZqwBFEj3+RAh1PZxfWcJnyoIM/SCrHzR/pEw2V+HvXoQPu1Y\n34MciPGrJhkwoyblSSoYc2S2XnnT02ptWYlEcqfpvuVt4ZHlBKJWVi/Lcg8GgiUh\njpvkuhEGr20sZQxbc9G3ILrLgmUKi9pSlDgDYzwvNbI8oS7EbD98dEyeghO/IEKO\ni20YOCxgNp3wJRFvtMXP40+N160y2X/B9oziRWO6DOWdMWXNXSv0nT3wM04pV3k9\nuXgFZ0tOS7/Rt1rB4PM6LIVdsN16ZhxXdUQljNdaepJYMO2jK8gsDwWB2iQgwjNM\nW5ruBvvSEGDK5i9F2UnRI7pVaewCMVah5OJ4kArc8E41UhQY9DnYyjZjmG7Ehe45\n8LVKGl19ZREtuJDp+R1N\n-----END CERTIFICATE-----\n'
files={'native-C-b9d-private.json':'/tmp/Cp2private.json','native-C-b9d-ack.json':'/tmp/Cp2a-fresh.json','integrated-state.json':'/tmp/qa-integrated-release-2fea1db74ba58758714063ec57be76e975fb0716/state.json','integrated-backup.log':'/tmp/qa-integrated-release-2fea1db74ba58758714063ec57be76e975fb0716/backup.log','integrated-deploy.log':'/tmp/qa-integrated-release-2fea1db74ba58758714063ec57be76e975fb0716/deploy.log'}
stream=io.BytesIO()
with tarfile.open(fileobj=stream,mode='w:gz') as archive:
 for name,filename in files.items():
  path=Path(filename)
  assert path.is_file() and not path.is_symlink() and path.stat().st_size<20000000,name
  archive.add(path,arcname=name,recursive=False)
cipher=pkcs7.PKCS7EnvelopeBuilder().set_data(stream.getvalue()).add_recipient(x509.load_pem_x509_certificate(CERT.encode())).encrypt(Encoding.DER,[pkcs7.PKCS7Options.Binary])
encoded=base64.b64encode(cipher)
print(json.dumps({'message':'Store encrypted integrated canonical release backup and final health receipt','branch':'codex/qa-live-browser-20261001','content':base64.b64encode(encoded).decode()}))
