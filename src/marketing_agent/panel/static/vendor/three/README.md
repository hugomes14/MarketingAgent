# Three.js local

Versao **0.186.1**; licenca MIT em LICENSE.

Origem: https://registry.npmjs.org/three/-/three-0.186.1.tgz

Integridade npm publicada e fixada: `sha512-blFeqb49wRCSGUGj7gtpfnSGHy2lwDk94RhUmS1c/hTby70kvChbWpkJ4Pm1390LqzzvTmzgXKHPEafJwCb8jA==`. Verificada antes de ler o tar.

A unica alteracao e reescrever from 'three' nas instrucoes import/export para
caminhos relativos ao build/three.module.js local. Comentarios e restante codigo intactos.

| Ficheiro | SHA256 origem | SHA256 local | Reescrito |
| --- | --- | --- | --- |
| `LICENSE` | 8b378ebe60e2fe500158cb0ac71cb5e8b7d92953c2abcc63a0eb90499653b5bc | 8b378ebe60e2fe500158cb0ac71cb5e8b7d92953c2abcc63a0eb90499653b5bc | nao |
| `build/three.core.js` | 9edde002b066a9a05676a6127f67735b62baf399bdea529f2f7e31657da769e6 | 9edde002b066a9a05676a6127f67735b62baf399bdea529f2f7e31657da769e6 | nao |
| `build/three.module.js` | 9052042d676cb0fdc1ddfefe193053f34b7ac0513a616fdac4535d49987812ea | 9052042d676cb0fdc1ddfefe193053f34b7ac0513a616fdac4535d49987812ea | nao |
| `examples/jsm/controls/OrbitControls.js` | 3d79d07ecb686b4e5d93232eedab255331c1beef711e13164eaa1f68655a5f2b | 2edf5d7099c54de0abb4b90873c98f35eb8984a56c83163527f885f907c17d3c | sim |
| `examples/jsm/environments/RoomEnvironment.js` | 55f466192cc84298755a424c5e040345006b2ee1455589b3b54126c2ea4123f4 | a5090a8742dd40ba17acf5b1b8a76e49c2044db591d2f7286149cc1625ff487d | sim |
| `examples/jsm/math/SimplexNoise.js` | 4b1455617135e6b477de76244de07547d95570643379b72f4758e50161b06072 | 4b1455617135e6b477de76244de07547d95570643379b72f4758e50161b06072 | nao |
| `examples/jsm/postprocessing/EffectComposer.js` | 4e079a5886152d7e529a59aef644e968ab4d32c6a33ce016b36bf29b2eac26f7 | 79a86f1e6c6a9be918b09b17936dc2dba12aaae8b5ccecdf8763ea57d162f23c | sim |
| `examples/jsm/postprocessing/GTAOPass.js` | a277f848d1035c751c54d9706e579aeb74689126ced25723e53d52ac3f26f445 | 0e5abd8054e61e25b00b072d68a39563433cd5c42533627d50bef29ffeaee3ab | sim |
| `examples/jsm/postprocessing/MaskPass.js` | 7cd08eee9d5d6f5578beaddbdcbe9c384f6873810af27f22ab7db3ceeb127aa3 | 7cd08eee9d5d6f5578beaddbdcbe9c384f6873810af27f22ab7db3ceeb127aa3 | nao |
| `examples/jsm/postprocessing/OutputPass.js` | 02e4a261af34de71338185e9e87f0cbe5cba9115608d984363e1269dec1d2272 | 905fff0f3b850a962dd675d13fe5c9420b3e36a332739451b8b669379c8389d8 | sim |
| `examples/jsm/postprocessing/Pass.js` | 444b409c235ead986893c472e720da1b779a56985c7d10b279c7944b52bd61c5 | f873ec11f08728ee1301aae7d47e1243574d124ee108213ef93c5c10458afc5b | sim |
| `examples/jsm/postprocessing/RenderPass.js` | 817f6c3cdcd0fd41515d112359ea0532568eefb5aabd3b33903957ebca1b8a6a | ad6c4ff61bf8339dd0604b96cb85d0c9642f1e06dc9ea4a1f476f0d01beec662 | sim |
| `examples/jsm/postprocessing/ShaderPass.js` | e2500a5913b26bbf5148ceaae644c6edcff06a18b01494ee37bf856353d2ab9d | 5df4d3e1384f5cb108fa23ecf7f534edd10e1631cd3db06c0dda557372dc7e8f | sim |
| `examples/jsm/renderers/CSS2DRenderer.js` | 256abfc6ae1aa20eb83c10d7a0e26534527df65872e4d52da6354bee78288c34 | 721b881a95e7583fb030f3a65d88c12d9c66952cf8656c805c10c86a333e05c2 | sim |
| `examples/jsm/shaders/CopyShader.js` | a33057d5ac91c43304c186ac0e8816e62bb2ed471d3a00ff3018dfd5c0389718 | a33057d5ac91c43304c186ac0e8816e62bb2ed471d3a00ff3018dfd5c0389718 | nao |
| `examples/jsm/shaders/GTAOShader.js` | cdea7be60452f61105375d2c18e4da2e8ce7b39a89246b1bb8fbf79319d58cfd | 78f0da4bd4f76f5dd0edc9555fb63482bfd60a36c4e93a45c2d1fba8bfdab58d | sim |
| `examples/jsm/shaders/OutputShader.js` | 353479f77a8d7e2629d49ccac9fc2f5dbfdda5442e0adf867b00377a2fcb0cb2 | 353479f77a8d7e2629d49ccac9fc2f5dbfdda5442e0adf867b00377a2fcb0cb2 | nao |
| `examples/jsm/shaders/PoissonDenoiseShader.js` | 61da074a9ad8b2139dd164c96b8b62c299f056ce06c216e1f707c5151d997602 | 2103dbdbcc8b2d56333a1fd061f1a2e313ed7f1d93ec0ca2b41d097ecaf63c96 | sim |

## Como atualizar

Confirmar a versao da referencia e a integridade no registo npm; atualizar constantes
e lista de ficheiros em scripts/vendor_three.py, rever a cadeia de imports e os testes.
Gerar numa pasta vazia com --dest, rever o diff e hashes antes de substituir esta pasta.
Executar pytest tests/test_vendor_three.py tests/panel/test_oficina_static.py.
Nao sao executados scripts npm e nao e necessario Node.
