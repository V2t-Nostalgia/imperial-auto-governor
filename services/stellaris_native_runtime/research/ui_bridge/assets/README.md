# Native UI prototype assets

`assistant_portrait_source.png` is the user-supplied source image for the
research-only right-side IAG launcher. The runtime version is a 512 x 512 DXT5
texture at `../gfx/interface/iag/assistant_portrait.dds`.

`thought_bubble_source.png` is the generated transparent source for the native
notification bubble. The runtime version is a 1024 x 512 DXT5 texture at
`../gfx/interface/iag/thought_bubble.dds`. Message text remains a native text
control, so the same bubble can display any Application response without
regenerating the image.

The image is not required by the Execution Broker or native action runtime.
Confirm redistribution rights before including it in a public production
release; replacing either presentation asset does not require changing the C++
bridge.
