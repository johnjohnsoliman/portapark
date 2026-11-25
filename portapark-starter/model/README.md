# Edge Impulse model

Put the exported Arduino library here as `SmartParkingSlot1-Proj_inferencing.zip`
(Edge Impulse Studio > Deployment > Arduino library > Build).

Install it in the Arduino IDE with Sketch > Include Library > Add .ZIP Library.

You can also train your own model: create an Edge Impulse project (Images, image classification),
upload photos of the slot labelled Car, Empty and Person, use 96 x 96 images with the Squash resize
mode, train with a small MobileNet, then export the Arduino library. Do not upload photos of people
to a public repository without their permission.
